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
stays immutable; the declaration must repeat it exactly. An optional captured
`declarations` path is also an immutable input. It feeds the
[owner declarations](footprint-libraries.md#owner-declarations) to the shared
parity pass unchanged. The binding reads the XML with the shared reader's
`pinless` mode. KiCad 10.0.7 omits `<pins>` for a pinless library part and
writes an empty `<pins/>` for each of its units. Those parts are admitted with
an empty inventory, and parity then requires a matching `pinless_symbols`
declaration. An empty `<pins/>` on a library part, or a unit with pins on a
pinless part, is still malformed. Other consumers of the reader stay strict.

## Runtime sequence

1. The earlier netlist producer must have `execution: ok` for this candidate and
   retain `design.xml`. Its sheet-instance count must equal the captured
   hierarchy. A failed or missing export gives `failed`/`unsupported` with
   outcome `unknown`.
2. The sandbox runs `python3 -m agent_ee.native_binding`. It reads the XML only
   through the strict [netlist parity](netlist-parity.md) reader
   (`native_netlist.read_native_inventory`). It reads captured libraries,
   footprints and every hierarchy sheet (root first) by explicit path. No
   network, model or host library is used.
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

Omitted `#` occurrences are audited from the captured sheets with the shared
S-expression reader (see [Occurrence identity](#occurrence-identity)). Each is
excluded only when its embedded definition (the one KiCad exports from)
carries `(power)`. A non-power omitted occurrence leaves board scope unproved:
`unsupported`, outcome `unknown`. A captured library's `power` or `on_board`
flag is recorded but never excludes an exported board instance. No reference
prefix exempts an exported component.

## Occurrence identity

The `netlist` producer runs this audit before crediting its export or writing
`connectivity.json`. It uses `native_netlist.read_native_instances`, the identity
projection of the shared bounded XML reader, and `native_netlist.hierarchy_gaps`,
which reuses the occurrence walker below. Missing, extra or ambiguous instances
stop the producer with `failed/unknown`; downstream native binding refuses that
prerequisite. The identity projection also covers pinless symbols and does not
apply the full electrical inventory's component-search limit.

A symbol occurrence is one sheet instance path and one symbol UUID. Owned
KiCad 10.0.7 exports, all with ordinary references so the choice is visible,
showed how the exporter picks an occurrence's reference:

| Instance data for the occurrence path | Exported reference |
|---|---|
| One entry | That entry |
| Several entries (any projects, or one project) | The last one in file order. Project names and a `.kicad_pro` change nothing |
| Entries only for paths outside this hierarchy | Another path's reference (in repeated sheets, a duplicate) |
| No instance data | The `Reference` property |

`#` omission follows the selected reference. Entries for paths outside the
hierarchy are ignored when the occurrence has its own entry. The `Reference`
property is not used when entries exist. A legacy `symbol_instances` block was
ignored for this file version.

The audit walks the hierarchy from the root sheet. Each child path is the
parent path plus the sheet UUID. The native sheet `tstamps` are the same chain
without the root UUID. It never takes the union of references. It never picks
by project name or by entry order:

| Condition | Finding |
|---|---|
| Exported occurrence | Native reference is authoritative; it must be one of the occurrence's own entries, if any, and must not begin with `#` (`occurrence_identity_conflict`) |
| Omitted, one distinct `#` reference in its own entries | Resolved; excluded only if power |
| Omitted, different references in its own entries (e.g. an archived project at the same path) | `ambiguous_occurrence_identity` |
| Omitted, no entry of its own | `missing_occurrence_identity` |
| Omitted, reference not beginning with `#` | `unexplained_omission` |
| Two omitted occurrences resolve to one reference | `duplicate_occurrence_reference` |
| Native component sheet/UUID not in the captured hierarchy | `native_identity_unmatched` |
| Native sheets differ from the captured hierarchy | `hierarchy_mismatch` |
| `symbol_instances` present | `legacy_symbol_instances` |

Each of these leaves board scope unproved: a direct binding audit gives
`unsupported`, outcome `unknown`; the netlist producer gives `failed/unknown`
before binding runs. A declared reference that matches no resolved occurrence is a
`ghost_declaration`, never an exclusion. Archived aliases for another root are
therefore neither exclusions nor problems. A captured sheet outside the
hierarchy, a missing child sheet, a cycle, a duplicate symbol UUID or a
malformed instance entry fails the audit (`failed`). Every record is retained
in the report's `virtual_symbols`.

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
Hierarchical designs follow the producer's sheet coverage. Only `#` omissions
are explained; any other captured occurrence missing from the export is
unknown. Same-path instance entries with different references are refused even
though the exporter picks the last one. This
check establishes symbol/footprint pad and assembly parity for the native board
inventory only. It does not choose packages or qualify land-pattern geometry,
mechanical fit, datasheet conformance, placement or manufacturing.

Owned fixtures in `tests/fixtures/native-binding/` were written with their
expected outcomes before implementation. Native tests export every variant with
`kicad-cli` and check the committed control export against a fresh one.
