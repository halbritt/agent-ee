# Captured direct-net topology

`netlist_extra` consumes a preceding successful `netlist` check, its exact
`design.xml` and `connectivity.json`, and a captured policy. Parameters are
exactly `netlist_check` and `policy`. The shared strict native XML reader owned
by the netlist-parity implementation supplies component instances, physical pin
inventories and native net codes. The legacy JSON projection is cross-checked
against those bytes; its flattened names never define electrical identities.

The captured policy has schema `agent-ee.netlist-extra-policy/1` and exactly
these fields:

- `requirement`: `{id, source: {path, locator}}`, matching the check's sole
  mapped baseline requirement and captured source.
- `classes`: map of exact `library:symbol` IDs to `{class, pins}`. Supported
  class names are `resistor`, `capacitor`, `inductor`, `ferrite`, `fuse`, `diode`
  and `switch`. `pins` contains two distinct physical pin numbers, which must
  exactly equal the complete selected native pin inventory.
- `overrides`: explicit `{component: {sheet, uuid}, symbol, rule}` entries,
  where `rule` has the same class/pins shape. Sheet is the exported UUID path;
  reference prefixes never establish a class.
- `exemptions`: explicit `{component: {sheet, uuid}, symbol, reason}` entries.
  These are reported as excluded, including intentionally bypassed or DNP
  networks. DNP alone does not authorize exclusion. An unresolved or changed
  exemption cannot silently waive a new component.
- `rail_pairs`: `{id, endpoints}` entries. Each endpoint is `{sheet, uuid, pin}`;
  the two physical terminals must be distinct. Rail name strings cannot anchor
  this requirement.

The [original fixture generator](../fixtures/netlist-extra/generate.py) provides
an executable policy example and creates a divider, switch, DNP debug component
and two actual hierarchical SDA islands. It uses independently authored symbol
geometry and no donor design files.

One violation is reported per declared two-terminal component whose pins share
a native net code, or per declared rail pair whose endpoints share one code.
Reports name the reference, sheet/UUID, exact pins, net ID/name and native
locators. Rail witnesses contain actual exported terminal membership. They do
not claim the geometric wire/label path, which native XML does not preserve.
These are violations of an explicit topology policy, not universal claims of
component damage or circuit failure.

Unknown classes, wrong pin inventories, unresolved rail endpoints or component
selectors keep aggregate coverage unknown, even when another component has a
known violation. Missing, failed, malformed or mismatched native prerequisites
produce `execution=failed`, `outcome=unknown`. Native resource bounds remain
unsupported. Native duplicate/contradictory identities cannot establish defects.

One advisory with `severity=warning` is emitted for each basename on distinct
native net IDs, retaining full names, sheet identities and codes. `/a/SDA` and
`/b/SDA` produce one SDA warning. A global SDA label connecting both sheets is
one native net and produces no SDA warning. The advisory's
`status=informational` means it never fails a requirement or supplies a defect
witness: separate local labels can be intentional. Use an explicit endpoint
relation check when a named connection is required. Single-node nets alone are
not defects.

Policy bytes are immutable baseline dependencies in scope/2 and scope/3;
schematic inputs remain mutable candidate bytes. Scope/3 exposes only declared
no-self-short and rail-pair predicates. `topology.json` binds the candidate,
policy, XML and connectivity hashes. Existing sealed coordinator findings and
public verification protect the report and prerequisite artifacts.

Tests execute the actual networkless native exporter for control, passive short,
switch short, merged rails, hierarchical islands, explicit overrides/exemptions,
unknown classes, principal identity and prerequisite failures, sealing and
tampering. They do not establish physical short behavior, PCB/copper paths,
low-impedance paths or an absence of native ERC violations.
