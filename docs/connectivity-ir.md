# Captured connectivity intent

`agent_ee.connectivity_ir` validates schematic intent before a native design
exists. It chooses no parts and writes no KiCad files. Its standalone report is
not a sealed coordinator witness. Native export and parity remain separate checks.

Run offline with explicit captured inputs and a fresh output directory:

```sh
PYTHONPATH=src python3 -m agent_ee.connectivity_ir intent.json \
  --captures captured-inputs --out build/intent-validation
```

The command reads only declared relative `.kicad_sym` paths beneath the capture
root. It refuses symlink captures and never searches installed KiCad libraries,
project tables, caches or environment variables. It retains `resolved.json` and
`manifest.json` with artifact hashes and status. Exit 0 means complete, passing
resolution; all rejected or incomplete results exit 1. Existing output directories
are refused. Supply captured stock libraries yourself; no third-party library
files are distributed with this feature.

## Input contract

The schema is `agent-ee.connectivity-intent/1`. All fields below are required;
`hierarchy`, `ports`, and component `part_binding` and `in_bom` are optional. Unknown fields
and duplicate JSON keys are malformed.

```json
{
  "schema": "agent-ee.connectivity-intent/1",
  "libraries": [{"name": "Device", "path": "symbols/Device.kicad_sym", "sha256": "REPLACE_WITH_64_LOWERCASE_HEX_DIGITS"}],
  "components": [
    {"id": "r", "reference": "R1", "lib_id": "Device:R", "value": "1k", "footprint": "", "units": [1]}
  ],
  "nets": [],
  "no_connects": [
    {"component": "r", "pin": {"number": "1"}},
    {"component": "r", "pin": {"number": "2"}}
  ]
}
```

A net is `{ "id": "vin", "name": "VIN", "terminals": [...] }`. A terminal
selects exactly one physical pin number or pin name, for example
`{"component":"u", "pin":{"name":"VI"}}`. Name selectors expand every matching
number in the selected units. Each physical pin must occur exactly once across
nets and explicit no-connects. Nets need two distinct terminals after expansion.
Logical component IDs and display references must each be unique; net IDs must
be unique. No pin is guessed, reassigned or silently deduplicated.

`in_bom` is an optional boolean component policy in both intent versions.
Resolution preserves the value and its source locator. The schematic emitter
uses it to override the library's BOM flag without setting DNP or removing the
part from the board. The older `schematic_emit` entry point refuses this optional
field. Native BOM exclusion does not yet qualify fab BOM/CPL
consistency; see [emitter limits](schematic-emitter.md#output).

`part_binding` is an optional opaque JSON object. This resolver preserves it
without granting sourcing or part-binding validation authority; callers must
validate any referenced binding through the separate binding contract.
Nonempty hierarchy and port declarations are preserved with unsupported coverage.
Version 1 cannot establish hierarchical connectivity.

## Resolved contract

`resolve_intent(intent, captured_libraries)` accepts a dictionary or UTF-8 JSON
bytes/text and an explicit path-to-bytes map. It has no filesystem access.
The output schema is `agent-ee.resolved-connectivity/1`. Components retain their
identity and selected units, all resolved pins, symbol source chain, and power
classification from the captured symbol's `power` declaration. Pin fields are
`number`, `name`, `electrical_type`, `unit`, `x_nm`, `y_nm`, `orientation`, `source`.
Coordinates are exact signed integer nanometres; angles are integer degrees in
0/90/180/270. Net terminals become `{component: logical_id, pin: number}`.

Inherited symbols recursively retain parent and child path/hash/symbol locators.
Missing parents and cycles cannot pass. Version 1 refuses derived replacement
pin graphics, alternate pin representations and repeated physical numbers across
selected units rather than guessing equivalence. Common-unit pins are selected
alongside the explicitly selected numbered units. Selected units must exist in
the inherited base symbol declarations, including units with graphics but no
unit-specific pins. A symbol with only common-unit declarations supports only
physical unit 1; common pins never authorize arbitrary unit numbers. Same-name pins with distinct
numbers remain distinct. Power classification never follows net-name heuristics.

The tokenizer handles quoted strings, escapes, semicolon comments and nested
lists. Library input is limited to 32 MiB, 128 nesting levels and two million
nodes. A library over the node limit fails closed with a
`library_coverage` finding that names the limit and the extraction route. Capture
the needed symbols with [large-symbol capture](symbol-capture.md) and declare the
subset library instead. Intent JSON is limited to 8 MiB and declaration arrays to 10,000 entries.
Only finite coordinates exactly representable in nanometres are supported.

`canonical_resolved(record)` produces deterministic compact UTF-8 JSON. The
`canonical_sha256` hashes that record with the hash field omitted. The
`intent_sha256` hashes validated intent after sorting libraries, components, nets,
terminals, no-connects and selected units; ordering is not connectivity identity.
Multiplicity is preserved. JSON decoding and re-encoding preserve resolved bytes.
A hash proves internal identity, not trusted production or correct engineering.
Consumers must call `validate_resolved(record)` before using it and bind their
own captured source provenance. The validator checks status, identity, unique
physical inventory and complete terminal membership.

## Status and qualification

| Case | execution | outcome | coverage |
| --- | --- | --- | --- |
| Complete valid intent | ok | pass | complete |
| Unknown pin/symbol, duplicate reference, singleton net, empty pins or unaccounted pin | ok | defect | complete |
| Non-JSON/schema-invalid generation response | malformed | unknown | missing |
| Missing capture, hash mismatch or unparseable library | failed | unknown | missing |
| Unsupported symbol representation or hierarchy | unsupported | unknown | missing |

When unsupported features and failed library captures coexist, execution is
`failed`, outcome is `unknown`, and coverage is `missing`; both findings remain.
Declaration ordering cannot change this classification.

Findings retain logical source locations and available pin inventories for unknown
pins. Partial results are diagnostics and cannot authorize emission or parity.
No model call is needed for validation or malformed-generation classification.
The resolver is standalone; registration in review scopes, native netlist parity,
and evidence sealing belong to coordinator integration.

Tests use original small libraries and dynamically read captured installed
`Device` and `Regulator_Linear` library bytes. The stock test asserts Device:R
pin 1 is passive and AMS1117-3.3 VI is physical pin 3 of type power_in, inherited
from AP1117-15. Strict mode (`AGENT_EE_REQUIRE_TOOLCHAIN=1`) fails if these stock
libraries are missing. It does not silently skip unexpected resolution failures.

## Opt-in hierarchy version 2

`agent-ee.connectivity-intent/2` requires all six arrays: `libraries`,
`components`, `nets`, `no_connects`, `hierarchy`, and `ports` (plus `schema`).
Version 1 retains its existing bytes, hash behavior and unsupported hierarchy
classification. Version 2 reuses its captured symbol and pin resolver.

An occurrence is `{id,parent,local_id,file}`. Exactly one root is
`{"id":"root","parent":null,"local_id":"root","file":"main.kicad_sch"}`.
Other occurrences name their immediate parent and a unique sibling `local_id`.
Scoped IDs and port fields are limited to 256 UTF-8 bytes to bound elaboration.
The connected acyclic tree supports at most 256 occurrences and 16 edges from
root to leaf. Each `file` is a normalized relative `.kicad_sch` path; absolute,
dot, parent, backslash and file/directory aliases are refused. Files must be
unique: repeated file instances remain unsupported pending actual native
instance qualification. Pure resolution does not access the filesystem;
the writer must also enforce precreated captured inventory and refuse symlinks.

Components add `sheet`; `(sheet,id)` identifies an occurrence-local component.
Annotated `reference` remains globally unique. Nets add `sheet`; `(sheet,id)`
identifies a local segment. Terminal selectors name components only in that
segment's sheet. Explicit no-connects add `sheet`. Direct cross-sheet terminal
references are refused. Physical components are limited to 1,024, local net segments to 2,048 and their
resolved pin inventory to 8,192, using the [shared graph ceilings](design-size-limits.md).
A resolver pass does not guarantee that graph matching will finish its search.

A port has exactly `child`, `id`, `name`, `direction`, `parent_net`, `child_net`.
It unions two declared segments on one immediate parent/child boundary. `id`
and emitted `name` are unique on that boundary. Directions are `input`,
`output`, `bidirectional`, `tri_state`, or `passive`; the emitter must qualify
these declarations through native hierarchical labels, sheet pins and ERC.
There are no root ports or ancestor shortcuts. Siblings connect through ports
to a shared parent segment. Pass-through segments may have zero direct pins.
Each final physical net still needs two distinct physical terminals, every pin
must be accounted for exactly once, and each boundary permits only one port
for a final physical net. Each port must reach terminals on both sides of the
boundary; port-only leaves are defects.

A child port's emitted hierarchical-label name must not equal a local label
on that child belonging to a different physical class after explicit port
unions. This includes generated reserved labels. Aliases within the same class
are allowed, and labels on other sheets do not collide. Raw validation and
resolved reconstruction both reject cross-class aliases as boundary defects;
recomputing a record's hash cannot bypass the check.

Local names are display labels, never global join keys. Equal nonempty names
on different segments in one sheet are refused; names may repeat across
sheets. Empty names receive `__agent_ee_` followed by SHA256 of the UTF-8 compact
JSON `[sheet,id]`; explicit names with that prefix are reserved and refused.
Hierarchical power symbols are unsupported for multi-sheet intents until native
global equivalence is explicitly qualified. Neither `GND` spelling nor power
pin type creates an implicit union.

Resolved `agent-ee.resolved-connectivity/2` retains the current status, source,
and hash fields. Physical component `id` is the compact JSON string
`[sheet,local_id]`; `sheet` and `local_id` remain explicit component fields.
Physical `nets` contain `{id,name,terminals}`, with globally scoped component
IDs and pin numbers. Their `name` is empty: a physical net has local aliases,
not one asserted universal native name. Its `id` is `net:` plus SHA256 of the
compact JSON sorted array of `[sheet,local_segment_id]` pairs. Hashing that
membership avoids quadratic output growth for large unions.

`local_nets` retains `{sheet,id,name,label,terminals,physical_net}` for every
source segment; terminals use resolved physical component IDs and pin numbers.
`physical_net` names the elaborated physical net. `hierarchy` and `ports` retain
the exact declaration fields. All collections are canonically ordered;
reordering input collections preserves identity, while changing a port binding
changes the intent and resolved hashes.

`validate_resolved` dispatches by version and checks the entire record:
scoped identities, tree, labels, local membership, explicit port unions,
boundary use, physical projection and complete pin accounting. Rehashing an
invalid projection does not make it valid. A structurally consistent authored
record still does not attest captured source or execution. Consumers must
freshly resolve the raw intent against captured library bytes before granting
emission or parity authority; existing resolved-source limitations remain.

The [native emitter](schematic-emitter.md) consumes this resolver interface and
has native two-level export, ERC, sheet-instance and mutation controls.
[Forward annotation](forward-annotation.md) consumes the native inventory;
hierarchical planning and binding are tested, but hierarchical application is
not yet qualified. Pure resolver tests alone do not qualify emission. Repeated
sheet files and multi-sheet power remain explicit unsupported coverage, and
full generation-campaign integration remains pending.
