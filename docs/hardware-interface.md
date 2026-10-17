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
segment when present, and the `_1`, `_2`, ... suffix KiCad appends when that
name collides with another net) represent disconnected pins, including explicit
no-connect markers. Their raw net codes, names and terminal locators remain in
the report; they cannot satisfy any conductor predicate. Normalization requires
exact native terminal/name agreement and one node, after duplicate/unknown-node
validation.
Local named nets retain their sheet-qualified names. Connected anonymous nets
retain membership; a name prefix alone never removes a conductor.

### Global labels spelled like generated no-connect names

A global label is unqualified, so it can deliberately name a singleton conductor
with exactly the generated spelling. KiCad still generates that spelling for a
truly disconnected pin when an identically spelled global label exists elsewhere.
Power symbols also name nets without a sheet prefix, through their Value.
Label text anywhere in the hierarchy is therefore never evidence. When a native
singleton carries a generated no-connect spelling that some captured global
label, power symbol Value or power pin name also uses, the check reads the
sheet file that owns the native component with the existing bounded
S-expression reader. The native component UUID must resolve to
exactly one source symbol whose hierarchical instance reference and `lib_id`
match the XML. The pin connection point comes from the embedded library pin,
rotated counterclockwise by 0, 90, 180 or 270 degrees and then mirrored about
`x` or `y`. All twelve orientations are checked against kicad-cli 10.0.7. The
result has three states:

- **Connected.** The label is exactly on the visible pin, or exactly on a wire
  reached through exact shared wire endpoints or junctions on both wires. The
  conductor is kept and the report cites the label, its position, UUID and route
  wires. Unmodelled items elsewhere in the file cannot break such an exact route.
- **Disconnected.** No such route exists, every top-level item in that sheet file
  is a modelled kind, and the conservative closure contains only wires, junctions
  and no-connect markers. That closure joins every item within 10 µm of the pin
  or of an item already joined. The label is reported as not reaching the pin,
  and the pin stays disconnected.
- **Unknown.** Every other case. This includes labels, sheet pins, hierarchical
  ports, directive labels, buses, bus entries or other pins within the margin.
  A power symbol on the pin is therefore unknown, because only global labels are
  modelled as positive sources. It also includes unmodelled item kinds,
  coordinates finer than the native 100 nm unit, oblique rotations, `extends`
  symbols and hidden or `no_connect` pins. Multi-unit components whose native
  UUIDs resolve to several placed symbols are also unknown.

kicad-cli connects labels lying within about one native unit of a wire. It does
not connect a pin lying inside a wire or a wire ending inside another wire
without a junction. Those cases and slightly offset labels are deliberately
unknown, not proved either way. An unknown pin is judged under both hypotheses.
A predicate that fails even if the label connects, such as `same_net` with a
singleton or a `named_net` that does not list the spelling, is a violation.
Otherwise the predicate is `not_evaluated`, so the check is `unsupported/unknown`.
Global-label connectivity through hierarchical ports is outside this qualified
domain. A spelling attached to a different conductor makes kicad-cli emit two
nets with one name. The reader refuses that as duplicate inventory instead of
choosing one.

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
ngspice response. Global-label qualification tests cover the off-pin label at
(140, 80) that the independent review reproduced. They also cover the same label
with an explicit no-connect marker (native `_1` suffix), a spelling attached to
another conductor, power symbols on and off the pin, and direct, wired,
junction and interior-wire labels; junctionless tees; pins inside wires; labels
offset by one native unit, 1 µm and 10 µm; sub-unit coordinates; all twelve pin
orientations; and child-sheet, root-sheet and hierarchical-port scoping. Wired,
unproven and orientation cases first check native kicad-cli connectivity with an
ordinary label at the same point. Scripted independent initial reviews and
central adjudication test evidence authority; they do not establish live-model
reliability or physical validation. Undeclared topology is outside this
contract's coverage.

The fixture includes an empty original board placeholder because the review CLI
requires a captured board path. No board check or PCB validity is claimed.
