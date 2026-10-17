# Native-bound footprint assignment

`footprint_assignment` with an ordered `netlist_check` binds the footprint check
to the candidate's actual native design. The captured declaration must then
enumerate every native board component exactly. Pad parity is evaluated on the
native selection, not on the declaration's copy of it. The predicate is
`footprint:native-assignment-parity/1`. Without `netlist_check` the historical
declared-only [`footprint:assignment-parity/1`](footprint-libraries.md) is
unchanged, and its reports cannot be relabeled as native coverage.

```json
{
  "id": "fp",
  "kind": "footprint_assignment",
  "params": {
    "manifest": "libs/footprints.json",
    "declaration": "assignment.json",
    "netlist_check": "netlist",
    "assembly_intent": [
      {"reference": "U1", "assembly": "smd", "connector": null},
      {"reference": "J1", "assembly": "through_hole", "connector": {"rows": 1, "columns": 4}}
    ]
  }
}
```

`netlist_check` must name an earlier `netlist` check. The manifest, every
footprint member and every declared symbol library are immutable scope/2 and
scope/3 inputs. The declaration and the schematic remain candidate data. The
native XML is producer evidence, never a baseline dependency. Assembly intent
stays immutable; the declaration must repeat it exactly.

## Runtime sequence

1. The earlier netlist producer must have `execution: ok` for this candidate and
   retain `design.xml`. Its sheet-instance count must equal the captured
   hierarchy. A failed or missing export gives `failed`/`unsupported` with
   outcome `unknown`.
2. The sandbox runs `python3 -m agent_ee.native_binding`. It reads the XML only
   through the strict [netlist parity](netlist-parity.md) reader
   (`native_netlist.read_native_inventory`). It reads captured libraries,
   footprints and schematic sheets by explicit path. No network, model or host
   library is used.
3. The coordinator requires the report's `native_xml_sha256` to equal the
   retained XML, and checks the report's sheet inventory against the producer.
   The report and the XML are sealed with the check.

## Board membership

These rules come from owned exports with KiCad 10.0.7 (`kicad-cli sch export
netlist`, XML and PCB-side formats):

| Native fact | Effect |
|---|---|
| `<property name="exclude_from_board"/>` (`on_board no`) | Excluded, reported individually. Absent from the PCB-side netlist. |
| `<property name="dnp"/>` | Still on the board. Footprint parity is required. `populated: false` is recorded. |
| `<property name="exclude_from_bom"/>` (`in_bom no`) | Recorded as `in_bom: false`. Not a board exclusion. |
| Reference starting with `#` | The exporter omits it from XML and PCB netlists, power or not. |
| Power symbol with another reference | Exported and placed on the PCB. Footprint parity is required. |

Omitted `#` instances are audited from the captured sheets with the shared
S-expression reader. Each is excluded only when its embedded definition (the
one KiCad exports from) carries `(power)`. A non-power omitted instance leaves
board scope unproved: `unsupported`, outcome `unknown`. A captured library's
`power` or `on_board` flag is recorded but never excludes an exported board
instance. No reference prefix exempts an exported component.

## Binding

Every native board component and every declared reference gets a binding
record with native and declaration locators:

| Condition | Binding | Check |
|---|---|---|
| Native board component with no declaration | `undeclared_native_component` | `unsupported`, unknown |
| Declaration absent from the native design | `ghost_declaration` | defect |
| Declared `lib_id` or `footprint` differs from native | `lib_id_mismatch` / `footprint_mismatch` | defect |
| Native libpart pins differ from the captured selected library | `library_pin_mismatch` | `unsupported`, unknown |
| Captured library or footprint member unreadable | `coverage` | `failed`, unknown |

`lib_id` comparison is exact; there is no alias or suffix matching. The native
libpart lists every pin of every unit, including unplaced units. It must equal
the shared resolver's complete captured inventory by number, name and type. The
only name rewriting accepted is what the exporter was observed to do: a name of
exactly `~` becomes empty and each space becomes `_`. Types must match exactly.
A same-named symbol whose embedded copy was edited is therefore unknown, not a
pass. A derived captured symbol qualifies when its flattened embedded copy
matches the inherited pins.

Parity then runs per bound reference through the existing
[footprint index and parity](footprint-libraries.md). All captured physical pins
are compared, including unconnected pins and unplaced units. Repeated thermal
numbers add no pin; paste-only and unnumbered NPTH pads are excluded; assembly
technology and declared connector rows*columns are checked. Shared unusable
footprints are reported for each reference, DNP or not.

Outcome precedence: library or export failure (`failed`/`malformed`), then
unestablished binding (`unsupported`), then any defect, then `pass`. With no
board components the outcome is `info`. Only `execution: ok` witnesses the
predicate (`details.evaluated_predicates`).

## Report

`work/<id>/native-assignments.json` (schema
`agent-ee.footprint-native-assignments/1`) records `native_xml_sha256`,
`native_inventory_sha256` over the native component and libpart projection,
`exporter`, `native_sheets`, `declaration_sha256`, per-reference `bindings`,
`exclusions`, `virtual_symbols`, the nested parity report with footprint index,
member and symbol-library hashes, and `findings`. The coordinator adds
`candidate_id`, `netlist_check` and `native_xml_sha256` to the check details.

## Strict reader additions

`read_native_inventory` exposes `on_board`, `in_bom`, `properties` and
`library_pins` per component, and `library_parts` and `exporter` per graph.
Duplicate `<footprint>` elements (either order), a footprint element with
children or attributes, a `Footprint` field that contradicts the element and
valued `dnp`/`exclude_*` flags are malformed. Absent or empty footprints are
the meaningful empty value.

## Limits

Exporter rules are qualified only for KiCad 10.0.7 and the owned probes.
Hierarchical designs follow the producer's sheet coverage. The omission audit
looks only for `#` references; other exporter omissions are not known. This
check establishes symbol/footprint pad and assembly parity for the native board
inventory only. It does not choose packages or qualify land-pattern geometry,
mechanical fit, datasheet conformance, placement or manufacturing.

Owned fixtures in `tests/fixtures/native-binding/` were written with their
expected outcomes before implementation. Native tests export every variant with
`kicad-cli` and check the committed control export against a fresh one.
