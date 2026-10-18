# Fab-package consistency gate

`fab_consistency` checks that a staged fab package describes the captured
board. Its only parameter is `export_check`, the ID of an earlier `fab_export`
check for the same candidate. That export check must declare its own earlier
`netlist_check`, and the scope must run at least one `geometry` check before
the gate. The gate never runs `kicad-cli` and never regenerates exports. It
reads these sources only:

- `work/EXPORT/fab/` and its `manifest.json` (`agent-ee.fab-manifest/1`).
  The export result must list `work/EXPORT/fab/manifest.json` as evidence.
- The export's sealed `kicad-cli version` execution record
  (`execution.json` with argv `["kicad-cli", "version"]` and its `stdout.log`).
- `work/NETLIST/design.xml` and `connectivity.json` from the export's netlist
  prerequisite. They must agree, and references must be unique.
- `work/GEOMETRY/geometry.json` from every earlier `geometry` check. Each must
  be bound to the captured board digest, and all must agree.
- The captured board bytes. The gate parses their S-expression for layers,
  footprint library IDs, sides, attributes, model paths and embedded files.

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
  geometry's non-DNP references less netlist DNP parts.

Scope/3 requires the parameters to be exactly `{export_check}`. The export,
its netlist and a geometry check must all come earlier in scope order. The check declares
the `fab-package:inventory`, `fab-package:designator-parity`,
`fab-package:lcsc-footprint`, `fab-package:cpl-side` and
`fab-package:3d-models` predicates.

## Results

| Condition | Result |
| --- | --- |
| Export missing, later in scope order, a different kind, a different candidate or not `execution=ok`; netlist prerequisite unverified; manifest not sealed by the export | `unsupported`, `unknown` |
| No earlier sealed geometry check; a geometry result not bound to the captured board digest; geometry without the footprint attribute fields; no kicad-cli on the sandbox PATH | `unsupported`, `unknown` |
| Manifest tool path or digest is not the pinned kicad-cli; manifest version differs from a sealed `kicad-cli version` run; a sealed version run that failed or whose output differs from its record | `malformed`, `unknown` |
| The export sealed no `kicad-cli version` run, so the manifest version is unverified | `unsupported`, `unknown` (a gap) |
| Board parse differs from the sealed geometry; geometry checks disagree; a non-board-only footprint is not in the netlist; a netlist component has no footprint and is not excluded from the board; duplicate netlist references; netlist XML and its connectivity record disagree | `malformed`, `unknown` |
| A board-only footprint that would be assembled (no DNP or BOM/position exclusion) | `unsupported`, `unknown` (a gap) |
| Staging directory absent or empty although the export reported ok | `malformed`, `unknown` |
| Manifest fields not exactly the fab-manifest/1 set; candidate, board path or board digest differs from the capture; `board_non_dnp` differs from the sealed geometry and netlist | `malformed`, `unknown` |
| A staged file not in the manifest, a manifest file not staged, a hash mismatch, an empty file, a symlink or other non-regular file; `bom.csv`, `cpl.csv`, `gerbers.zip` or `pcb_summary.json` absent | `malformed`, `unknown` |
| Manifest copper count or enabled layers differ from the board. The enabled layers must be every board copper layer, `F.Mask`, `B.Mask` and `Edge.Cuts`, plus optional paste and silkscreen layers. | `malformed`, `unknown` |
| Fewer Gerber plots than enabled fab layers; an enabled layer with no plot whose `FileFunction` names it; two plots for one layer | `malformed`, `unknown` |
| Some plots carry no `FileFunction` attribute, so a layer cannot be matched | `unsupported`, `unknown` |
| Excellon files other than exactly the manifest PTH and NPTH files; a `MixedPlating` file; a plating attribute that contradicts its role; an unrecognized file in `drill/` | `malformed`, `unknown` |
| An Excellon file has no plating attribute | `unsupported`, `unknown` |
| `gerbers.zip` unreadable, or its members are not exactly the staged `gerbers/` and `drill/` bytes | `malformed`, `unknown` |
| CSV not UTF-8, wrong header, wrong cell count, no rows, broken quoting, or a non-finite CPL number | `malformed`, `unknown` |
| Compressed range (`R1-R4`), duplicate, empty or invalid designator; BOM quantity differs from its designator count; empty or invalid LCSC number; non-ASCII BOM cell | `ok`, `defect` |
| A designator missing from the BOM, the CPL or the assembled board set. The defect names the designator and each missing side. | `ok`, `defect` |
| One LCSC number maps to more than one BOM footprint or board footprint library ID | `ok`, `defect` |
| CPL `Layer` differs from the board footprint side, or is not Top/Bottom | `ok`, `defect` |
| An assembled board footprint that the sealed netlist marks DNP or excluded from BOM/position files | `ok`, `defect` |
| A model path that resolves to a missing captured input. An embedded model that is absent, has no data, is not base64, is not one complete zstd frame with a declared content size, decodes to nothing, or lacks a matching checksum. | `ok`, `defect` |
| A model path through a library variable other than `KIPRJMOD`, an absolute path outside the captured sources, or a path that escapes the inputs. Several differing embedded records with one name; an embedded frame that needs a dictionary or decodes to more than 64 MiB. | `unsupported`, `unknown` |

The assembled board set holds every footprint without `dnp`,
`exclude_from_bom` or `exclude_from_pos_files`. Defects take priority over
coverage gaps. Remaining gaps are listed in `details.coverage_gaps` and the
limitations. When no defect remains, any gap makes the result unknown. It is
never a pass.

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

Stock KiCad model libraries are not captured. A board that uses
`${KICAD10_3DMODEL_DIR}` therefore gives an unknown result until a later
contract maps library roots to captured inputs.

## Evidence

`work/CHECK_ID/fab-consistency.json` holds the plot, drill, designator, model,
defect and gap records. A defect citation points at the export's `bom.csv` or
`cpl.csv` when the export sealed that file. Otherwise it points at this report.
Every citation stays within this check's workspace or a sealed prerequisite,
so the evidence seal accepts the result.

## Limits

- CPL coordinates and rotations are not compared with the board. Pad-centroid
  midpoints and the rotation policy belong to the rotation lane, and the
  fab-manifest/1 contract does not fix the export origin.
- The gate does not parse Gerber geometry, run DFM checks or read build logs.
  Plot identity comes only from `FileFunction` attributes.
- Model resolution proves that a captured file, or a decodable embedded
  payload with a matching checksum, exists. It does not check the model
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
A test confirms that pcbnew reproduces it byte for byte. One test exports real
KiCad 10.0.7 drill and Gerber files from a synthetic board. It confirms that
the plating and `FileFunction` classifiers accept native output. These tests
need KiCad, pcbnew and bubblewrap; `AGENT_EE_REQUIRE_TOOLCHAIN=1` turns a
missing toolchain into a failure instead of a skip.

The fab-export tests also run the real netlist, geometry, export and consistency
checks together on the owned fabrication fixture, then seal and verify their
evidence. This covers the producer-to-consumer manifest and tool-identity
contract without substituting either handler.
