# Fab-package consistency gate

`fab_consistency` checks that a staged fab package describes the captured
board. Its only parameter is `export_check`, the ID of an earlier `fab_export`
check for the same candidate. That export check must declare its own earlier
`netlist_check`. The gate never runs `kicad-cli` and never regenerates
exports. It reads these sources only:

- `work/EXPORT/fab/` and its `manifest.json` (`agent-ee.fab-manifest/1`).
  The export result must list `work/EXPORT/fab/manifest.json` as evidence.
- `work/NETLIST/design.xml` from the export's netlist prerequisite.
- The captured board bytes. The gate parses their S-expression for layers,
  footprint references, sides, attributes and model paths.

Scope/3 requires the parameters to be exactly `{export_check}`. The export
and its netlist must both come earlier in scope order. The check declares
the `fab-package:inventory`, `fab-package:designator-parity`,
`fab-package:lcsc-footprint`, `fab-package:cpl-side` and
`fab-package:3d-models` predicates.

## Results

| Condition | Result |
| --- | --- |
| Export missing, later in scope order, a different kind, a different candidate or not `execution=ok`; netlist prerequisite unverified; manifest not sealed by the export | `unsupported`, `unknown` |
| Staging directory absent or empty although the export reported ok | `malformed`, `unknown` |
| Manifest fields not exactly the fab-manifest/1 set; candidate, board path or board digest differs from the capture; `board_non_dnp` differs from the board and netlist | `malformed`, `unknown` |
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
| A model path that resolves to a missing captured input, or a missing embedded model | `ok`, `defect` |
| A model path through a library variable other than `KIPRJMOD`, an absolute path outside the captured sources, or a path that escapes the inputs | `unsupported`, `unknown` |

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
- `kicad-embed://NAME` must name an embedded file in the footprint or the board.

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
- Model resolution proves that a captured file exists. It does not check the
  model content or fit.

The tests use synthetic boards, netlists, models and staging trees. Stub
`netlist` and `fab_export` handlers write those bytes, so only this consumer is
under test. One toolchain-gated test exports real KiCad 10.0.7 drill and Gerber
files from a synthetic board. It confirms that the plating and `FileFunction`
classifiers accept native output. `AGENT_EE_REQUIRE_TOOLCHAIN=1` turns a
missing toolchain into a failure.
