# Rung-1 board build (experimental stand-in)

`agent_ee.board_build` builds an unplaced KiCad 10 board from a schematic
project and its native XML netlist, places it with the landed
[deterministic placer](placer.md), and checks the result with native
`kicad-cli pcb drc --schematic-parity`.

This module is a **private experimental stand-in** for the rung-1 driver. Its
schemas are `agent-ee.rung1-envelope/0-experimental` and
`agent-ee.rung1-board-build-receipt/0-experimental`, and every receipt states
`"qualification": "experimental-unqualified"`. At integration, the owned
implementations supersede it: forward annotation (AGENTEE-52, branch
`impl/native-emitter`) and the placement writer step with its placer adapter
(AGENTEE-91). The owned board envelope record is `agent-ee.envelope/1`
(AGENTEE-85). The module registers no check kind and grants no acceptance
authority.

## Command

```sh
python3 -m agent_ee.board_build PROJECTDIR --netlist NETLIST.xml --footprints FPDIR \
  --envelope ENVELOPE.json --template-board TEMPLATE.kicad_pcb \
  --placement-request REQ.json --out OUTDIR [--placement-fallback row]
```

| Exit | Meaning |
| --- | --- |
| 0 | Placed. The judge passes and the placed board has zero schematic-parity items. |
| 1 | Input refused. The reason is on stderr and no receipt is written. |
| 2 | Not placed. The placer returned a typed result such as `unsupported_geometry`, and the row stand-in was not requested or not applicable. |
| 3 | Placed with defects (`outcome: defect`): the judge failed or parity items exist. For the row stand-in, any other violation also gives this result. |
| 4 | The placement writer failed. Its `NAME-failure-*` diagnostics are kept under `OUTDIR/placement/`. |
| 5 | A native tool failed. This is missing coverage, never a pass. |

The command is meant to run under the networkless executor sandbox, with
inputs below `/candidate` and `OUTDIR` below `/work`. It never writes outside
`OUTDIR`, and it refuses an `OUTDIR` that exists and is not empty. The tests
run it directly and also under `executor.execute`.

In the sandbox, the copied `fp-lib-table` resolves
`${KICAD10_FOOTPRINT_DIR}` to `/candidate/stock/footprints`. Put the
footprint libraries at that path and pass the same path as `--footprints`.
If they are absent, native DRC reports `lib_footprint_issues` for every
footprint. The row stand-in counts these as violations (exit 3). This was
observed with the RC inputs: 3 `lib_footprint_issues` without the mount, and
0 violations with it, with a board byte-identical to the host build.

## Inputs

- `PROJECTDIR` contains exactly one `NAME.kicad_pro`, its `NAME.kicad_sch`
  (and any other `.kicad_sch` files), and optionally `sym-lib-table` and
  `fp-lib-table`. These files are copied into each output project. A board
  already in `PROJECTDIR` is ignored.
- `NETLIST.xml` is the native `kicad-cli sch export netlist --format kicadxml`
  export of that schematic. Its `design/source` basename must be one of the
  project's schematics. Each component must have one explicit `LIB:NAME`
  footprint, one symbol UUID and a sheet path. Each `(ref, pin)` terminal must
  be on one net only, and a symbol's declared pins must equal its net nodes.
  A net class other than `Default` is refused, because this builder does not
  carry schematic net-class assignments. The `exclude_from_board` property is
  also refused.
- `FPDIR/LIB.pretty/NAME.kicad_mod` is loaded with `pcbnew.FootprintLoad`. If
  the file is missing, the build is refused. No other library is searched.
- `TEMPLATE.kicad_pcb` and its sibling `TEMPLATE.kicad_pro` are both
  required. The builder keeps the template's layers, setup, stackup and
  plot settings, and removes all of its footprints, copper, zones, drawings
  and groups with the native `BOARD.RemoveAll`. The output `.kicad_pro` is the
  project's file with its `board` and `net_settings` sections replaced by the
  template's sections. If these sections are already equal, the file is a
  byte copy.
- `ENVELOPE.json` (`agent-ee.rung1-envelope/0-experimental`) has exactly these
  keys:

```json
{"schema": "agent-ee.rung1-envelope/0-experimental",
 "outline_mm": {"x": 100, "y": 100, "w": 50, "h": 30},
 "copper_layers": 2, "stackup": "template-2-layer",
 "fixed_refs": [{"ref": "J1", "x_mm": 103.81, "y_mm": 112.46, "rotation_deg": 0, "side": "F"}]}
```

  The outline becomes four Edge.Cuts segments with a 0.1 mm stroke. The
  `copper_layers` value must equal the template's copper layer count.
  `stackup` is a declared label only: the stackup comes from the template,
  and nothing is made from the label. Each fixed reference is placed, rotated
  by a quarter turn, flipped with `SetLayerAndFlip` when `side` is `B`, and
  locked.
- `REQ.json` contains `policy` (`agent-ee.packing-policy/1`) and
  `authorization`, which has exactly `{"schema": "agent-ee.placement-write/1",
  "single_design_writer": true}`. The policy's `fixed_refs` must equal the
  envelope's fixed references. The builder binds the authorization to the
  board that it built: `source_sha256` is the hash of the unplaced board, and
  `writable_root` is `OUTDIR/placement`. The bound request is written to
  `OUTDIR/placement-request.json`.

## Construction rules

For each component, the builder loads the footprint and sets its `LIB_ID`,
reference and value. The other netlist fields become hidden footprint
fields. The builder also sets the symbol path, the sheet name and file, and
the `dnp` and `exclude_from_bom` flags. Each netlist pin number gives its
net to every pad that has the same number. A pin without a pad is refused,
and the refusal lists the real pads. A numbered pad that is not a pin in the
netlist is also refused. Movable parts are put in a row to the right of the
outline, in reference order. The placer then moves them.

Identical inputs give byte-identical boards. Construction runs in a new child
interpreter that seeds KiCad's KIID generator (`KIID.SeedGenerator`) from the
input hashes. One process cannot repeat the UUID stream, because the seed is
process-global and the footprint plugin caches parsed libraries. The tests
compare boards from two in-process builds and from one build in the sandbox.

## Outputs

| Path | Content |
| --- | --- |
| `unplaced/NAME.kicad_pcb` and the project files | Board before placement |
| `placement/proposal/` | The placer's published directory: candidate, plan, probe, judgment, native DRC |
| `placement/placement-plan.json` | The typed placer result, only when placement did not happen |
| `placed/NAME.kicad_pcb`, project files, `placement-result.json` | Placed board, a byte copy of the placer's candidate |
| `checks/{unplaced,placed}/drc-parity.json` | Raw `kicad-cli pcb drc --schematic-parity --severity-all` report |
| `checks/placed/placement-judgment.json` | `placement.judge` on the saved placed board |
| `construct/` | Construction spec and child stderr |
| `build-receipt.json` | Receipt |

DRC runs on a copy of each project under `checks/`, because `kicad-cli` writes
`.kicad_prl` next to the board it reads. The builder checks that the copy was
not changed. Parity fails closed: the report must contain the
`schematic_parity`, `violations`, `unconnected_items` and `ignored_checks`
lists, and none of the core parity checks may be ignored. KiCad ignores
`footprint_filters_mismatch` by default, and the receipt records that.
Unconnected items are expected on an unrouted board, and the receipt records
their count.

The receipt contains the sha256 of the netlist, template board and project,
envelope, request, each project file and each footprint file. It also
contains the bound request, the hashes of all output files, and the tool
identities: the pcbnew version, build version and `_pcbnew` binary sha256,
the `kicad-cli` path, sha256 and version, and this module's source sha256.
It records the status, outcome, placer step counts and reason, the judgment
verdict, and the parity counts and items.

## Stock pad shapes

The merged placer supports the stock pads used by this control (AGENTEE-109).
`R_0805_2012Metric` and `C_0805_2012Metric` have round-rectangle SMD pads;
the 1x03 header has rectangular and circular through-hole pads. The native
build places these footprints, preserves their pad shapes, sizes and nets,
and retains exact schematic parity. Unsupported pad geometry still produces
a typed unknown rather than a passing placement.

The tests show the placed path with owned placeholder copies of the three
footprints in which every pad is rectangular. The placeholders exist only in
the test's temporary directory. They are not production footprint models,
and native DRC reports them as `lib_footprint_mismatch`. The packer and the
judge use pad centres and courtyards only, not pad outlines.

## Row stand-in (`--placement-fallback row`)

The row stand-in is **unqualified**. It predates the stock-pad support in
AGENTEE-109. The current stock control uses the qualified placer even when
this fallback flag is supplied. Without the flag, behaviour is unchanged.

The stand-in is used in one case only: the landed placer returns
`unsupported_geometry` with the pad-shape reason. Any other placer result
remains `not_placed`.

The stand-in places each movable footprint as follows:

- All movable footprints are in one row on the outline's horizontal centre
  line. The courtyard bounding box of each footprint is centred on that
  line.
- The footprints are in reference order, from left to right. The row starts
  at the right courtyard edge of the rightmost fixed reference, or at the
  left edge of the outline, plus the request's `gap_nm`. Adjacent courtyards
  are separated by `gap_nm`.
- Rotation is 0 and the side does not change.

A fresh pcbnew child applies the row to a copy of the unplaced board in
`placement/row-standin/`. Fixed (locked) parts do not move. The stand-in
then runs the landed `placement.judge` on the saved board and native parity
DRC on a copy of the project.

The board is published as `placed/` (exit 0) only when all of these are true:

- The judge verdict is `pass`.
- There are zero schematic-parity items.
- There are zero violations other than unconnected items.

A judge `hard_fail`, a parity item or any other violation gives
`placed_with_defects` (exit 3). The evidence stays under
`placement/row-standin/` and `checks/row-standin/`, and nothing is
published as placed.

The receipt records the following:

- `"placer": "rung1-row-standin-experimental"`
- `"placer_reason": "placer.pack refused stock pad shapes (AGENTEE-109)"`
- `judge_verdict`
- The placer's own typed result
- The row placements, the judgment and the parity counts

`placement-result.json` uses the schema
`agent-ee.rung1-row-standin-result/0-experimental`. The row ignores
wire length, decoupling, connector edges and everything else that the packer
optimises. It shows only that the stock-footprint board can be placed and
checked.

With the fixture inputs, the row puts C1 at (107.78, 115.00) mm and R1 at
(111.66, 115.00) mm, and J1 stays fixed. The judge verdict is `pass`. Parity
DRC finds 0 parity items, 0 violations and 4 unconnected items. Two builds
give byte-identical boards.

## Tests

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest tests.test_board_build -v
```

[test_board_build.py](../tests/test_board_build.py) exports the fixture
schematic's netlist with `kicad-cli` from a temporary copy of
`fixtures/rc/control`. The native tests skip with the standard toolchain
prerequisite reason when the toolchain is absent. The tests check the
following:

- The unplaced board has three footprints whose pad nets equal the XML
  terminals and the shared rung-1 connectivity. Stock pads are placed without
  changing their shapes or sizes.
- The placed board passes `placement.judge` and has zero schematic-parity
  items.
- Boards are byte-identical in process and in the sandbox.
- A pin without a pad is refused.
- A footprint that is missing from FPDIR is refused.
- An `OUTDIR` that is not empty is refused.
- Envelope, request and netlist validation work as specified.
- With stock footprints, enabling the row fallback still uses the qualified
  placer and gives a judge pass, zero parity
  items, zero violations other than unconnected items, and byte-identical
  boards from two builds. The row arithmetic is checked separately.
