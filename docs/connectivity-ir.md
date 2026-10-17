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
`hierarchy`, `ports`, and component `part_binding` are optional. Unknown fields
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
alongside the explicitly selected numbered units. Same-name pins with distinct
numbers remain distinct. Power classification never follows net-name heuristics.

The tokenizer handles quoted strings, escapes, semicolon comments and nested
lists. Library input is limited to 32 MiB, 128 nesting levels and two million
nodes. Intent JSON is limited to 8 MiB and declaration arrays to 10,000 entries.
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
