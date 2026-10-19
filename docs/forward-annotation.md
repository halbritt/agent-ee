# Forward annotation

`agent_ee.forward_annotation` (AGENTEE-52) pushes a native KiCad netlist into
an existing placed and routed board without the KiCad GUI. It is a helper of
the sole implementation writer. It neither places nor routes: new footprints
land on a staging column beside the board outline. Its plan and result records
are diagnostic. Native DRC with schematic parity and the coordinator's checks
judge the board it writes.

```sh
PYTHONPATH=src python3 -m agent_ee.forward_annotation plan project/board.kicad_pcb \
  --netlist design.xml --manifest footprints.json --captures captured \
  --work build/plan-work --record build/plan.json
PYTHONPATH=src python3 -m agent_ee.forward_annotation apply project/board.kicad_pcb \
  --netlist design.xml --manifest footprints.json --captures captured \
  --plan build/plan.json --out project/board.kicad_pcb --backup-dir build/backup \
  --work build/apply-work --record build/apply.json
```

Both commands exit 0 on success and 1 on refusal, and write their record to a
new file. `--work` must name a new directory.

## Inputs and binding

- Native netlist: the actual `kicad-cli sch export netlist --format kicadxml`
  output for the schematic. The complete component inventory, sheet paths,
  unit UUIDs and nets come from AGENTEE-41's `agent_ee.native_netlist` reader;
  this module has no other inventory source. Symbol fields such as
  `Description` (which native parity compares) are read from the component
  elements that the same reader's bounded parser returns, and they must cover
  exactly the same references. Do-not-populate and board-excluded symbols are
  refused, because they are not qualified yet. Both come from the reader's
  parsed `populated` and `on_board` fields: KiCad 10.0.7 exports `on_board no`
  as a valueless `exclude_from_board` property of the component, so quoting,
  whitespace, character references and end-tag form do not matter. The reader
  rejects a flag that carries a value.
- Board: the existing `.kicad_pcb`. pcbnew runs only in
  `agent_ee.forward_annotation_probe`, a child process started through the
  production Bubblewrap executor with networking unshared and the board copied
  into a read-only input directory.
- Footprints: an `agent-ee.footprint-libraries/1` manifest and its captured
  files, indexed by AGENTEE-36 `index_footprints`
  ([footprint libraries](footprint-libraries.md)). Every component's footprint
  field must name an exact manifest member whose electrical pads equal the
  symbol's pins. There is no library search and no package selection.

A footprint binds to a component only through its native path: the
component's sheet path plus any one of its unit UUIDs. References are never
used for binding. A footprint without a path is kept as board-only. A path that
matches no component is planned for removal.

## Plan

Schema `agent-ee.forward-annotation-plan/1`. The plan binds the SHA-256 of the
board, netlist, manifest, board facts and each footprint file used, and lists:

| Field | Meaning |
| --- | --- |
| `add` | New components with exact footprint member, native path, fields and staging position |
| `remove` | Bound footprints whose component is gone |
| `update` | Retained footprints whose reference, value or symbol fields change |
| `retain`, `board_only` | Unchanged bindings, and footprints with no native path |
| `renames` | Board nets whose retained pad membership equals exactly one netlist net with a new name |
| `new_nets`, `pads` | Nets to create, and the complete pad-to-net assignment with previous values |
| `stale_nets` | Nets no longer in the netlist that tracks, vias or zones still use; they are kept, and DRC reports them |
| `counts` | Counts for each list, plus `pad_changes` |

Staged footprints are placed in reference order in one column that starts
5.08 mm right of the board outline, with anchors on the 1.27 mm grid.

## Apply

`apply` treats the supplied plan only as a declaration. It recomputes a fresh
plan over the current bytes and refuses any difference (`stale_plan`), so an
edited plan, a changed board or netlist, or a changed footprint capture is
refused before pcbnew runs. If the plan makes no change, the output is a
byte-for-byte copy of the input. Otherwise the child:

- renames each `NETINFO_ITEM` in place, so tracks, vias, zones and pads keep
  their binding;
- creates new nets, removes footprints, and updates references, values and
  fields;
- loads each added footprint from the exact captured bytes, sets its path,
  fields and staging position, and seeds its UUIDs from the native path;
- assigns every planned pad net.

A separate child process then reads the saved board. The parent refuses
publication unless the footprint inventory matches the plan, retained
footprints and pads have unchanged positions, orientation, side and identity,
added footprints match the plan, every pad has its planned net, and every
track, via and zone keeps its UUID and its (renamed) net. Existing routing and
zone fills are never edited or refilled, and tracks of removed footprints stay
in place for DRC to report. For an in-place apply, the source bytes are first
copied to `--backup-dir`, which must lie outside the board's directory so that
the backup never enters a captured candidate's file inventory.

## Publication

Destinations are checked before any work and written without following links:

- Every component of the output directory and of the backup directory is
  opened from `/` with `O_NOFOLLOW`. A link anywhere in either path is refused
  (`unsafe_path`), and later writes go through the opened directory, so a
  component swapped during apply cannot redirect them. Missing backup
  directories are created with mode 0700.
- The output is staged in a new file in the output directory, with a random
  name, created exclusively without following links. The staged file takes the
  source board's permission bits, and it is always removed.
- A new output is published by a hard link that never replaces an existing
  entry. An existing output in any form, including a dangling link, is refused
  (`output_exists`). An in-place target must be a regular file, not a link, and
  it is replaced atomically.
- The backup is named by the SHA-256 of the source board. An existing backup
  entry is reused only if it is a regular file with exactly those bytes.
  Otherwise, including a link to identical bytes, the apply is refused
  (`backup_location`).
- The published bytes are read back without following links and compared with
  the verified bytes.

## Refusals (before any mutation)

`native_netlist`, `board_exclusion_unsupported`,
`dnp_unsupported`, `missing_footprint` (no assignment, or not an exact manifest
member), `footprint_library_coverage` (unreadable, missing or changed manifest
member), `pad_pin_mismatch`, `duplicate_native_path`,
`multiunit_binding_ambiguous`, `footprint_change_unsupported` (a bound footprint
whose identity differs from the symbol's), `plan_rejected`, `stale_plan`,
`stale_input`, `backup_location`, `output_exists`, `unsafe_path`,
`native_child` and `verification`.

## Evidence

`tests/test_forward_annotation.py` uses the original routed fixture
`fixtures/generation/regulator.kicad_pcb`: three placed footprints, tracks, two
vias and a filled GND zone on B.Cu. Native DRC with schematic parity against
the emitted regulator schematic reports no violations, no unconnected items and
no parity issues. With KiCad 10.0.7, using the existing `geometry_probe` and
raw copper S-expressions for independent extraction, the tests show:

- Adding one capacitor adds exactly one footprint, with its full pad and net
  inventory, on the staging grid. Every existing footprint pose, pad and copper
  item is unchanged. DRC parity passes, and the new part's unrouted pads are
  reported as unconnected. Reapplying the same inputs is a byte-identical no-op.
- Renaming the ground net renames pads, tracks, both vias and the zone in
  place, with no geometry change. DRC is clean.
- Removing the output capacitor removes its footprint and keeps its copper.
  DRC reports the retained track, and parity passes.
- A missing footprint, an unreadable manifest member, a duplicate native path,
  multi-unit ambiguity, a tampered plan, a changed netlist or board, and a
  backup inside the board directory are each refused. The board bytes stay
  unchanged and no backup is written.
- A checks-only review of an annotated board passes ERC, DRC and netlist
  checks. `verify` rejects a changed DRC report, a changed tool identity and a
  changed source board.
- Board exclusion is refused for an actual native export of a schematic with
  `on_board no`, and for the same export respelled with single quotes, a
  newline before `name`, spaces around `=`, a character reference and an
  explicit end tag. The shared reader accepts each spelling as the same
  document. A field whose text mentions the flag is not refused. An actual
  do-not-populate export is refused as `dnp_unsupported`.
- For the native export of the emitted two-level design, child-sheet symbols
  bind only under their full native sheet path plus unit UUID.
- Publication leaves a planted link or file at the former fixed staging name
  untouched, refuses a live or dangling link at the output name, refuses a
  link in an output or backup ancestor and a linked in-place board, and
  refuses a backup name that is a link or holds different bytes. In each case
  no file outside the requested output and backup is written.

`agent_ee.native_netlist` and `agent_ee.netlist_parity` (AGENTEE-41) are
imported directly by these tests, so a missing module is an error, never a
skip.

## Limits

- Changing a footprint for a retained component, DNP and board exclusion are
  refused. Planning against the native export of an emitted two-level design
  binds child symbols by their full sheet path, and the same symbol UUID under
  another sheet path is not adopted. Applying to a hierarchical board has not
  been tested natively.
- Staging is not placement. Placement and routing quality, and fab readiness,
  are outside this helper.
- Net renames need an exact one-to-one match on retained pad membership.
  Splits and merges are pad reassignments: the old copper keeps its net and
  appears as a stale net for DRC to report.

## Native net-name encoding (AGENTEE-220)

Both forward annotation and `board_build` use `agent_ee.board_net_names.from_xml`
only at the XML-to-board boundary. KiCad 10.0.7's XML exporter decodes a literal
slash in a net name, whereas its native S-expression exporter and PCB parity
retain `{slash}`. Global names and generated no-connect names therefore encode
`/` as `{slash}` before planning board nets. Braces (including `~{...}` overbar
markup), spaces, colons, quotes and backslashes remain unchanged. This is net-name
encoding, separate from S-expression string quoting. Already encoded slash tokens
stay encoded. Names that collide after conversion are refused before writing.

Declared sheet-name prefixes remain structural paths. XML loses the distinction
between extra local-name slashes and sheet separators: a local label `A/B` and a
net `B` in sheet `A` can export the same name. Local names with a slash after the
most specific declared sheet prefix are refused rather than guessed. Global
labels deliberately shaped like absolute sheet paths are outside this XML-only
adapter's qualified input domain. Supporting those inputs would require an
escaped native-netlist input, which this XML-only adapter does not provide. Native XML inventories, topology parity,
and schematic label text keep their original representation. Scaffold writes
no schematic nets and needs no conversion. The emitter continues to accept its
existing safe-label grammar; generated NC names are produced by KiCad itself.

Every consumer that compares raw XML net names with saved PCB pad nets applies
the same rule rather than its own conversion: `placer_runner` binds the XML
export to the saved board through `from_xml`, so placement after forward
annotation and the placement step inside `board_build.build` accept the board
form. `probe_features.validate_board` compares a probe's saved pad net with
`board_net_names.encode_net(native_net)`: the declared `native_net` stays the
exact XML name, and only the board side is encoded. With one declared name and
no sheet table, a local name is taken verbatim; an ambiguous local slash then
cannot match the `{slash}` form KiCad saves, so the check still refuses it.
Consumers that read only XML (`netlist_parity`, probe native identity, part
bindings) or only the board (DRC, fab inventory, constraint binding) compare
names within one domain and need no conversion. Declared names checked against
the board, such as net-constraint nets and placement `ground_nets`, are board
names and must already use `{slash}` for a literal slash.

`test_native_net_name_escaping_matches_native_export_and_parity` modifies only
an original owned fixture's emitted NC pin name and global label. It compares
native XML and S-expression exports, applies both board producers, reloads their
outputs in fresh processes, checks pad copper layers, and requires zero native
schematic-parity mismatches. Reannotation has no net changes. Refusal tests cover
encoding collisions and ambiguous local slashes; the hierarchical fixture checks
that declared sheet separators match the native exporter. After annotation the
same test binds the raw XML to the saved board with the placer's binding and
refuses a board carrying literal slashes. `test_slash_global_net_builds_places_and_passes_native_schematic_parity`
(`tests/test_board_build.py`) runs the whole build and placement with a global
`OUT/2` net; `test_slash_pin_name_no_connect_annotates_and_places`
(`tests/test_pod_placement.py`) annotates a pod-like no-connect pin named
`SDA/~{SPI_CS}` and places the result in the sandbox; and
`test_saved_pad_net_is_the_board_form_of_the_declared_native_net`
(`tests/test_probe_features.py`) covers probe pads on global, local and
no-connect names.
