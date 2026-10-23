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
  A terminal may instead contain exactly `unresolved` with a nonempty reason;
  every predicate using it remains `not_evaluated`. An entirely unresolved
  io-map contract may have an empty `components` mapping when every invariant
  has a CSV citation; uncited component-free contracts are refused.
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

Local invariant sources may also carry `sha256`, exactly 64 lowercase hexadecimal
characters over the source file bytes. Each local source path must declare the
same digest on every invariant or omit it on every invariant. This consistency
check applies even without source bytes. Legacy sources without it remain valid.
The [compiler and validation options](io-map.md#requirements-source-provenance) can require it;
when source bytes are supplied, validation refuses missing bytes or a mismatch.
The hardware-interface capability verifies declared digests against captured
requirement files while retaining exact baseline `path` and `locator` matching.
A requirement file with a declared digest also becomes an immutable input.
Digest-free requirement files retain their existing input policy.
This optional field keeps schema `agent-ee.interface/1`; it adds provenance
without changing predicate semantics or the scope baseline schema.

The supported kinds and additional fields are:

| Kind | Fields | Predicate |
| --- | --- | --- |
| `component` | `component`, Boolean `populated` | Required component exists with declared assembly state. |
| `value` | `component`, string `equals` | Exact value equals declaration or an explicitly allowed edit. |
| `named_net` | `terminal`, nonempty `names` | Terminal belongs to one exact permitted native net name. |
| `same_net` | Two unique `terminals` | Both known terminals belong to the same native conductor. |
| `different_net` | Two unique `terminals` | Both known terminals belong to distinct native conductors. |
| `no_connect` | `terminal` | Known terminal has no conductor; ambiguous singleton classification is unknown. |
| `unresolved` | Nonempty `reason` | Always `not_evaluated`; records an obligation that cannot yet be checked. |
| `indirect_path` | Ordered `terminals`, `pin_names`, `devices`, `directions` | Every declared segment and intermediate part identity holds natively; see [below](#declared-indirect-paths). |

The offline [io-map compiler](io-map.md) emits these predicates from a CSV.
Invariants may carry its strict `io_map` citation object (requirement entries carry an enforced [qualification](io-map.md#requirement-qualification)), including a captured
CSV hash, row and external destination text. The CSV becomes an immutable scope
input; destination prose supplies no additional engineering authority.

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

### Declared indirect paths

An `indirect_path` invariant declares a route from a connector terminal to an MCU
terminal through active or passive parts. It never treats a part as a short:

- `terminals` is an ordered list: connector, then an enter and exit terminal for
  each intermediate part, then the MCU terminal. All are declared terminals.
- `pin_names` gives the exact native library pin name of each terminal. Unnamed
  passive pins use the empty string.
- `devices` has one `{component, value, role}` per enter/exit pair. Both of
  that pair's terminals must belong to `component`. The endpoints cannot be
  intermediate parts. `value` must exactly equal the native component value; `role` is text.
- `directions` has one entry per conductor segment (connector to the first
  enter terminal, each exit to the next enter, last exit to MCU):
  `toward_mcu`, `toward_connector` or `bidirectional`.

The path is `satisfied` only when all of the following hold:

- Each segment's two terminals are connected members of one native net.
- Each intermediate part is populated with its declared value.
- Each pin name matches.
- Each part's enter and exit pins are on different nets, so the part is not bypassed.
- The connector and MCU terminals are on different nets.
- The native library electrical pin types agree with each declared direction.
  A driver must be `output`, `bidirectional`, `tri_state`, `passive`,
  `open_collector` or `open_emitter`. A receiver must be `input`,
  `bidirectional` or `passive`. Both ends of a `bidirectional` segment must be
  `bidirectional` or `passive`.

Any contradiction is a `violation` with each failed element named. These include
a severed segment, a wrong value or pin name, a bypassed part, connector-MCU
continuity or a contradicted direction. Omitting an intermediate part therefore
fails as a severed segment. An unresolved terminal, an unsupported symbol mapping,
a `free`/`unspecified`/missing native pin type or an absent pin name stays
`not_evaluated`. A contract that also asserts `same_net` between a declared path's
endpoints is refused. Records add
`segments`, `devices` and this disclaimer, which is also a check limitation:
topology only; no transceiver, amplifier, divider or ADC transfer, accuracy or
stability, CAN bit rate, firmware or BLE behavior is claimed.

### Global labels spelled like generated no-connect names

A global label is unqualified, so it can deliberately name a singleton conductor
with exactly the generated spelling. KiCad still generates that spelling for a
truly disconnected pin when an identically spelled global label exists elsewhere.
Power symbols also name nets without a sheet prefix, through their Value.
kicad-cli 10.0.7 expands text variables such as `${PROBE:REFERENCE}` or
`${<symbol UUID>:REFERENCE}` in both global labels and power Values before it
names the net, so `unconnected-(${PROBE:REFERENCE}-Pad1)` on the pin exports
`unconnected-(PROBE-Pad1)`. Source text anywhere in the hierarchy, present or
absent, is therefore never evidence. Whenever a declared terminal's native
singleton carries a generated no-connect spelling, whatever the source text, the
check reads the sheet file that owns the native component with the existing
bounded S-expression reader. The native component UUID must resolve to
exactly one source symbol whose hierarchical instance reference and `lib_id`
match the XML. The pin connection point comes from the embedded library pin,
rotated counterclockwise by 0, 90, 180 or 270 degrees and then mirrored about
`x` or `y`. All twelve orientations are checked against kicad-cli 10.0.7. The
result has three states:

- **Connected.** A global label whose literal source text equals the exported
  name is exactly on the visible pin, or exactly on a wire
  reached through exact shared wire endpoints or junctions on both wires. The
  conductor is kept and the report cites the label, its position, UUID and route
  wires. Unmodelled items elsewhere in the file cannot break such an exact route.
- **Disconnected.** No such route exists, every top-level item in that sheet file
  is a modelled kind, and the conservative closure contains only wires, junctions
  and no-connect markers. That closure joins every item within 10 µm of the pin
  or of an item already joined. Isolation does not depend on any item's
  spelling, so a far-away label or power symbol with a text variable cannot
  rescue the pin. Any literally matching label is reported as not reaching the
  pin, and the pin stays disconnected.
- **Unknown.** Every other case. This includes labels, sheet pins, hierarchical
  ports, directive labels, buses, bus entries or other pins within the margin.
  A power symbol on the pin is therefore unknown, because only global labels are
  modelled as positive sources. So is a label with text variables on or near the
  pin's conductor: its expansion is not reimplemented, so it never proves a
  connection. It also includes unmodelled item kinds,
  coordinates finer than the native 100 nm unit, oblique rotations, `extends`
  symbols and hidden pins of any library type other than `no_connect`.
  Multi-unit components whose native UUIDs resolve to several placed symbols
  are also unknown.

A pin whose embedded library type is `no_connect`, hidden or visible, is never
joined by name, so no label ever makes it connected. It is disconnected only
when three source facts hold: every connectable item in the owning sheet file is
a modelled kind; exactly one explicit `(no_connect (at ...))` marker sits exactly
on the pin's connection point; and the 10 µm closure around the pin contains
nothing but that marker. Any wire, bus, bus entry, junction, label, global or
hierarchical label, directive label, sheet pin, other pin or second marker in
the closure leaves it unknown, as do a missing or offset marker and any
unmodelled item. The report cites the marker position and UUID under
`global_label_sources`. Hidden pins of other types may join nets implicitly and
stay unknown.

kicad-cli connects labels lying within about one native unit of a wire. It does
not connect a pin lying inside a wire or a wire ending inside another wire
without a junction. Those cases and slightly offset labels are deliberately
unknown, not proved either way. An unknown pin is judged under both hypotheses.
A predicate that fails even if the label connects, such as `same_net` with a
singleton or a `named_net` that does not list the spelling, is a violation.
Otherwise the predicate is `not_evaluated`, so the check is `unsupported/unknown`.
This applies to every declared generated singleton, labelled or not: one in a
sheet file with unmodelled items, on a hidden pin or on a multi-unit component
leaves `different_net` `not_evaluated`, while `same_net` and unlisted names
still fail. Undeclared pins are not classified.
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
orientations; and child-sheet, root-sheet and hierarchical-port scoping.
Library `no_connect` pin tests cover visible and hidden pins isolated by their
own marker, natively and in source, and each failing condition: no marker, an
offset or doubled marker, an unmodelled item, and wires, junctions, labels, bus
entries, a second marker or an exactly spelled global label in the margin. Wired,
unproven and orientation cases first check native kicad-cli connectivity with an
ordinary label at the same point. Scripted independent initial reviews and
central adjudication test evidence authority; they do not establish live-model
reliability or physical validation. Undeclared topology is outside this
contract's coverage.

The fixture includes an empty original board placeholder because the review CLI
requires a captured board path. No board check or PCB validity is claimed.
