# Fab-package consistency gate

`fab_consistency` checks that a staged fab package describes the captured
board. Its required parameter is `export_check`, the ID of an earlier `fab_export`
check for the same candidate. That export check must declare its own earlier
`netlist_check`, and the scope must run at least one `geometry` check before
the gate. The optional `model_libraries` parameter maps stock 3D-model variables
to captured, pinned model libraries (see [Stock model libraries](#stock-model-libraries)). The gate never runs `kicad-cli` and never regenerates exports. It
reads these sources only:

- `work/EXPORT/fab/` and its `manifest.json` (`agent-ee.fab-manifest/1`).
  The export result must list `work/EXPORT/fab/manifest.json` as evidence.
- The export's sealed `kicad-cli` execution records, including its version
  run (`execution.json` with argv `["kicad-cli", "version"]` and `stdout.log`),
  and the hash-bound `console.log` of each recorded native step.
- `work/NETLIST/design.xml` and `connectivity.json` from the export's netlist
  prerequisite. They must agree, and references must be unique.
- `work/GEOMETRY/geometry.json` from every earlier `geometry` check. Each must
  be bound to the captured board digest, and all must agree.
- The captured board bytes. The gate parses their S-expression for layers,
  footprint library IDs, sides, attributes, model paths and embedded files.
- The captured stock-model receipt for each declared `model_libraries` variable,
  and the captured library files that it lists.

Board-only mechanical admission is replayed from the captured board and sealed
netlist: all three native exclusion attributes, no netlist path/component and
no assigned pad net. A present `designators.mechanical` list must equal the
recomputed sorted references; missing legacy lists do not bypass admission.
Netlisted test pads with footprint exclusions use the existing declared
`JLCPCB_Assembly=not_assembled` rule and exclusion replay.

The manifest is a claim to verify, never an authority:

- Tool identity. `tool.path` must be the kicad-cli that the executor sandbox
  resolves on its fixed PATH, and `tool.sha256` must be that binary's digest
  (the pinned-tool rule that `measurements` uses). `tool.version` must equal
  the output of every sealed `kicad-cli version` run of the export.
- Board inventory. Footprint references, sides and the `dnp`,
  `exclude_from_bom`, `exclude_from_pos_files` and `board_only` attributes
  come from the sealed pcbnew geometry probe. The board parse must match the
  geometry exactly. Every non-board-only footprint must be in the sealed
  netlist, and every netlist component must be on the board unless the netlist
  excludes it from the board. `designators.board_non_dnp` must equal the
  geometry's non-DNP references. Schematic DNP flags do not change this
  board-derived manifest field. The producer takes DNP from the board alone
  (see [DNP authority](fab-export.md#dnp-authority)). This gate compares the
  sealed netlist `dnp` property with the geometry `dnp` attribute for every
  designator in both. Each disagreement, in either direction, is a
  `designator_parity` defect, listed in `details.designators.dnp_disagreements`
  as `{"designator", "schematic_dnp", "board_dnp"}`. The producer's own
  `details.dnp_disagreements` is not trusted for this.
- Assembly exclusions. `designators.exclusion_rule` must equal the
  `assembly_exclusion` parameter of the captured export check (or `null`).
  The gate re-derives the excluded set with the producer's rule function from
  the sealed netlist fields and the captured board footprint properties (see
  [assembly exclusions](fab-export.md#assembly-exclusions)), and
  `designators.excluded` must equal it exactly. Excluded designators are not
  assembled for parity: one that appears in the BOM or CPL is a
  `designator_parity` defect. `details.designators` records `excluded` and
  `exclusion_rule`.

Scope/3 accepts `{export_check}` and an optional `model_libraries`. Any other
parameter is unsupported. Each declared receipt is a captured, immutable
baseline input. The export,
its netlist and a geometry check must all come earlier in scope order. The check declares
the `fab-package:inventory`, `fab-package:designator-parity`,
`fab-package:lcsc-footprint`, `fab-package:cpl-side`,
`fab-package:cpl-coordinates`, `fab-package:3d-models` and
`fab-package:build-log` predicates.

## Results

| Condition | Result |
| --- | --- |
| Export missing, later in scope order, a different kind, a different candidate or not `execution=ok`; netlist prerequisite unverified; manifest not sealed by the export | `unsupported`, `unknown` |
| No earlier sealed geometry check; a geometry result not bound to the captured board digest; geometry without the footprint attribute fields or exact `position_nm`; no kicad-cli on the sandbox PATH | `unsupported`, `unknown` |
| Manifest tool path or digest is not the pinned kicad-cli; manifest version differs from a sealed `kicad-cli version` run; a sealed version run that failed or whose output differs from its record | `malformed`, `unknown` |
| The export sealed no `kicad-cli version` run, so the manifest version is unverified | `unsupported`, `unknown` (a gap) |
| Board parse differs from the sealed geometry; geometry checks disagree; a non-board-only footprint is not in the netlist; a netlist component has no footprint and is not excluded from the board; duplicate netlist references; netlist XML and its connectivity record disagree | `malformed`, `unknown` |
| A board-only footprint that would be assembled (no DNP or BOM/position exclusion) | `unsupported`, `unknown` (a gap) |
| Staging directory absent or empty although the export reported ok | `malformed`, `unknown` |
| Manifest fields not exactly the fab-manifest/1 set; `headers` differing from the captured JLCPCB templates; candidate, board path or board digest differs from the capture; `board_non_dnp` differs from the sealed geometry; `exclusion_rule` is unknown or not the captured export rule; `excluded` is not the set re-derived from the sealed netlist and board, or the sources do not support it | `malformed`, `unknown` |
| A staged file not in the manifest, a manifest file not staged, a hash mismatch, an empty file, a symlink or other non-regular file; `bom.csv`, `cpl.csv`, `gerbers.zip` or `pcb_summary.json` absent | `malformed`, `unknown` |
| Manifest copper count or enabled layers differ from the board. The enabled layers must be every board copper layer, `F.Mask`, `B.Mask` and `Edge.Cuts`, plus optional paste and silkscreen layers. | `malformed`, `unknown` |
| Fewer Gerber plots than enabled fab layers; an enabled layer with no plot whose `FileFunction` names it; two plots for one layer | `malformed`, `unknown` |
| Some plots carry no `FileFunction` attribute, so a layer cannot be matched | `unsupported`, `unknown` |
| Excellon files other than exactly the manifest PTH and NPTH files; a `MixedPlating` file; a plating attribute that contradicts its role; an unrecognized file in `drill/` | `malformed`, `unknown` |
| An Excellon file has no plating attribute | `unsupported`, `unknown` |
| `gerbers.zip` unreadable, or its members are not exactly the staged `gerbers/` and `drill/` bytes | `malformed`, `unknown` |
| CSV not UTF-8, header not equal to the captured JLCPCB BOM/CPL template (`Comment` column in BOM), wrong cell count, no rows, broken quoting, or a non-finite CPL number | `malformed`, `unknown` |
| Compressed range (`R1-R4`), duplicate, empty or invalid designator; BOM quantity differs from its designator count; empty or invalid LCSC number; non-ASCII BOM cell | `ok`, `defect` |
| A designator missing from the BOM, the CPL or the assembled board set. The defect names the designator and each missing side. | `ok`, `defect` |
| A designator whose sealed netlist DNP state differs from its board footprint DNP state, in either direction (`designator_parity`) | `ok`, `defect` |
| One LCSC number maps to more than one BOM footprint or board footprint library ID | `ok`, `defect` |
| CPL `Layer` differs from the board footprint side, or is not Top/Bottom | `ok`, `defect` |
| CPL X or Y differs from the native footprint anchor by more than 0.01 mm in the `fab_export/1` coordinate frame | `ok`, `defect` |
| A recorded native producer execution lacks a sealed console log | `unsupported`, `unknown` |
| A recorded native producer execution failed, or its console hash differs from the execution record; console text is not UTF-8 | `malformed`, `unknown` |
| A native export console has a warning, error or fatal diagnostic | `ok`, `defect` |
| An assembled board footprint that the sealed netlist marks DNP or excluded from BOM/position files | `ok`, `defect` |
| A model path that resolves to a missing captured input. An embedded model that is absent, has no data, is not base64, is not one complete zstd frame with a declared content size, decodes to nothing, or lacks a matching checksum. | `ok`, `defect` |
| A model path through a library variable other than `KIPRJMOD` that no `model_libraries` entry maps, an absolute path outside the captured sources, or a path that escapes the inputs. Several differing embedded records with one name; an embedded frame that needs a dictionary or decodes to more than 64 MiB. | `unsupported`, `unknown` |
| A stock-variable model that is not in its declared receipt, has no pinned upstream revision (`existing-local`), is not captured, or differs from its receipt hash; a receipt that is not the captured input or is invalid | `unsupported`, `unknown` |
| `model_libraries` empty, not an object, mapping a variable that is not `KICADn_3DMODEL_DIR` or `KISYS3DMOD`, or naming a path that is not a captured `.agent-ee-models.json` | `unsupported`, `unknown` |

The captured export check may declare [`copper_features`](fab-export.md#copper-features),
for example `["JP1"]` for an owned solder jumper. The gate revalidates its
surface copper/mask/paste pads, silk/fab graphics, both native exclusions,
and absence of models, and repeats the copper field allow-list across footprint,
captured schematic/variant and retained native fields (including duplicates). It reads
the sealed native netlist directly; a producer pass or its summary cannot
establish this exception. Invalid declarations are `unsupported/unknown`.
A qualified feature stays in the sealed board inventory but out of the
assembled set. Its presence in either CSV is a parity defect. Existing
undeclared exclusions retain their verdicts.

Graphics and properties carrying a `(layers ...)` node are refused, including
when a separate `(layer ...)` names an admitted decorative or mask layer.
KiCad can interpret the extra token as plotted copper in either fab gate.

Qualification also admits courtyard graphics and bounded own-side mask openings
under the [exporter's shape rule](fab-export.md#copper-features). Both gates use
the same pad/copper envelope and 0.05 mm margin; courtyard size is excluded from
that envelope.

The assembled board set holds every footprint without `dnp`,
`exclude_from_bom` or `exclude_from_pos_files`. Defects take priority over
coverage gaps. Remaining gaps are listed in `details.coverage_gaps` and the
limitations. When no defect remains, any gap makes the result unknown. It is
never a pass.

CPL coordinates use the `fab_export/1` native position-file convention: absolute
board X and negated board Y, for both top and bottom footprints. The origin is
the board origin, with no auxiliary/drill-origin override. The anchor is
`pcbnew.FOOTPRINT.GetPosition()`, not a calculated pad centroid. The geometry
probe retains its integer nanometres as `position_nm`; the gate compares CSV
decimals against them without first rounding to the probe's four-decimal
`position_mm`. Exactly 0.01 mm on either axis is allowed; any larger difference
is a defect naming the reference. A legacy probe without exact positions is
unknown coverage and must be rerun.

The gate reads every sealed `kicad-cli` execution's console and verifies its
`console_sha256`. Warning/error/fatal diagnostics at the start of a line or
followed by a colon are defects; a missing log is a coverage gap. Ordinary
progress lines containing a filename such as `warning.kicad_pcb` are not
diagnostics. The exporter retains all five native step records and logs.

Model paths resolve as follows:

- `${KIPRJMOD}` and relative paths resolve against the captured board's
  directory and must name a captured input.
- An absolute path resolves only when it equals the original source path of a
  captured input.
- `kicad-embed://NAME` resolves in the footprint's embedded files first, then
  the board's. A name alone is not enough. The gate repeats a native KiCad 10
  load on the captured bytes ([kicad_embedded.py](../src/agent_ee/kicad_embedded.py)):
  base64 decode, a pure-Python zstd decode (the frame must declare its content
  size), and a checksum comparison. A 32-digit checksum is KiCad's MurmurHash3
  variant (seed `0xABBA2345`, tail zero-padded to 4 bytes); a 64-digit
  checksum is SHA-256. Native pcbnew drops a record whose checksum fails and
  discards a record without data when it saves.
- `${KICAD10_3DMODEL_DIR}/...` and other stock variables resolve only through
  `model_libraries`, described below. The gate never reads a host KiCad
  installation or the process environment.

## Stock model libraries

Stock KiCad footprints name their models through a stock variable, for example
`${KICAD10_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0805_2012Metric.step`.
The pinned toolchain does not install these models. The owner therefore
captures a stock-model cache built by
[`render3d --fetch-models`](pcb-renders.md#component-models)
(`models3d.fetch_stock_models`) as an ordinary scope input. Such a cache holds
the `agent-ee.stock-models/1` receipt `.agent-ee-models.json`. Each receipt entry
records a file's SHA-256, its size, and either the kicad-packages3D commit and
URL it was downloaded from or `existing-local`. The scope then maps each
variable to that captured receipt:

```json
{"id": "fab-consistency", "kind": "fab_consistency",
 "params": {"export_check": "fab",
            "model_libraries": {"KICAD10_3DMODEL_DIR": "lib3d/.agent-ee-models.json"}}}
```

Variables are `KICADn_3DMODEL_DIR` or `KISYS3DMOD`. One receipt may serve
several variables only if the scope maps each one explicitly. The gate never
treats `KICAD9_3DMODEL_DIR` as an alias of `KICAD10_3DMODEL_DIR`. A model resolves when all of
these hold:

1. The path is exactly `${VARIABLE}/Library.3dshapes/file.step` (or `.stp` or
   `.wrl`), using the `models3d` stock-path rules. `$(VAR)` forms, nested
   directories and `..` are refused.
2. The variable is declared, and its receipt is the captured input and passes
   `models3d` receipt validation.
3. The receipt lists the file with a pinned kicad-packages3D commit.
   `existing-local` entries are unpinned.
4. The file next to the receipt is a captured input whose bytes match both the
   capture digest and the receipt SHA-256.

The model record then carries `sha256` and
`library: {variable, receipt, member, commit}`. `details.models.libraries`
lists each declared receipt and its captured digest. Any failure is an
`uncovered` record and a `model_coverage` gap. The gate never reports it as a
`model_unresolved` defect, because a receipt lists only the files fetched for
one board, not the complete library. The receipt is
frozen in the baseline, and its hashes bind every member. Editing a member
leaves that model unknown. Editing the receipt changes the baseline.

The receipt records where a file was fetched from. The gate does not fetch
again and does not compare the bytes with upstream, so the claim is "captured
bytes equal the pinned receipt", not "upstream holds these bytes". Captured
`${KIPRJMOD}`, relative and absolute models also record their captured
`sha256`.

## Evidence

`work/CHECK_ID/fab-consistency.json` holds the plot, drill, designator, model,
coordinate, build-log, defect and gap records. A defect citation points at the
export's `bom.csv` or `cpl.csv` when the export sealed that file. Otherwise it points at this report.
Every citation stays within this check's workspace or a sealed prerequisite,
so the evidence seal accepts the result.

## Limits

- CPL rotation and assembly centroid/offset corrections belong to the rotation
  lane. This check verifies native footprint anchors only.
- The gate does not parse Gerber geometry or run DFM checks.
  Use the separate [fab round-trip check](fab-roundtrip.md) for drill/flash
  conservation and outline bounds.
  Plot identity comes only from `FileFunction` attributes.
- Model resolution proves that a captured file, or a decodable embedded
  payload with a matching checksum, exists. For stock models, it proves that the
  captured file matches its pinned receipt. It does not check the model
  content or fit.
- The tool check pins the binary by path and digest. The version string is
  checked only against the export's own sealed version run; the gate does not
  run kicad-cli itself.
- No policy yet covers assembled board-only parts, so they stay unknown.

The focused consistency tests stage synthetic package bytes through
a stub `fab_export` handler to inject faults. That stub records a real sandboxed
`kicad-cli version` run, and its manifest names the actual pinned binary. Every
case runs the real `geometry` check (pcbnew in the sandbox) on the captured
board, so no case stubs the board inventory. Synthetic boards have no
schematic, so their `netlist` is a stub that the gate cross-checks against that
geometry. The parity ground-truth cases run the real `netlist` check: on the
owned RC control (R1, C1, J1) and on the owned pins schematic (U1 only) under a
board that claims more parts. `fixtures/rc/embedded-models/` holds the RC board
re-saved by pcbnew with owned embedded models; see `fixtures/rc/PROVENANCE.md`.
A test confirms that pcbnew reproduces it byte for byte.
The stock-library cases use the RC control board, whose stock KiCad 10
footprints keep their `${KICAD10_3DMODEL_DIR}` paths. They capture a receipt
and owned placeholder STEP members, never KiCad library models. The cases cover
a pass, a missing root, an undeclared variable, an unlisted, uncaptured,
mismatched or unpinned member, a malformed receipt, and a `KICAD9_3DMODEL_DIR`
root. A scope/3 native-pipeline case freezes the receipt in the baseline. One test exports real
KiCad 10.0.7 drill and Gerber files from a synthetic board. It confirms that
the plating and `FileFunction` classifiers accept native output. These tests
need KiCad, pcbnew and bubblewrap; `AGENT_EE_REQUIRE_TOOLCHAIN=1` turns a
missing toolchain into a failure instead of a skip.

`NativeFabPipelineTests` in [test_fab_consistency.py](../tests/test_fab_consistency.py)
runs the actual `netlist → geometry → fab_export → fab_consistency` chain on the
owned [fab-export fixture](../fixtures/fab-export/generate.py), on two and four
copper layers, with an original captured model file. Scope/3 separates the
informational native inventories from the package acceptance requirement.
Fault injection changes retained files before coordinator sealing. The log
diagnostic test adds an explicitly synthetic warning to a retained native log;
it does not claim a warning from the native tool. Defect cases exercise BOM/CPL/board parity,
compressed/duplicate/empty references, LCSC binding and footprint conflicts, CPL
side and coordinate violations, missing models and synthetic log diagnostics.
A native source-mutation case also makes the schematic and board disagree on
J1 DNP state in both directions. The producer succeeds with its board-derived
package, so BOM, CPL and board agree. The gate reports the disagreement as a
named `designator_parity` defect. When the schematic marks a populated
footprint DNP, it also reports `netlist_exclusion_assembled`. It does not treat
the valid manifest as stale.
A rule-exclusion case marks J1 `JLCPCB_Assembly=not_assembled` in the
schematic and on the board, declares the rule on the export and passes with J1
re-derived as excluded. Putting J1 back into the CPL is a `designator_parity`
defect, and a manifest whose `excluded` list was emptied is `malformed`.
Each is sealed and accepted through the existing requirement/check witness and
central adjudication helpers. Controls, malformed packages, stale producer
outputs and missing native evidence cannot qualify a defect witness. No witness
schema or reviewer independence rule changes.

The fab-export tests also run the real netlist, geometry, export and consistency
checks together on the owned fabrication fixture, then seal and verify their
evidence. This covers the producer-to-consumer manifest and tool-identity
contract without substituting either handler.
