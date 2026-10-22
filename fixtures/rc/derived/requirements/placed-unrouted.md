# Control-derived RC placed-unrouted: declared fixture requirements

These expectations were authored before any check ran on the derived boards,
independently of native/check output. They characterize a deliberately incomplete
stage input; they do not waive any acceptance check. A seeded failure is a
production defect.

The board is derived from `fixtures/rc/control` (origin T1) by
`fixtures/rc/derived/generate.py` through pcbnew. The circuit is
J1.1 (/IN) -- R1 10k -- /OUT (J1.2) -- C1 100n -- GND (J1.3).

Saved boundary: Every footprint at its control position (J1 100,100; R1 105,100; C1 110,100 mm). The five control tracks and the GND via are removed. The control's B.Cu GND zone is kept with its outline and settings and saved unfilled.

- **R-erc**: Native ERC has no issues. Expected initial outcome: `pass`.
- **R-netlist**: Native XML exports the three-component RC network. Expected initial outcome: `info`.
- **R-drc-placement**: Native placement violations and schematic parity have no issues; unrouted connectivity is ungraded. Expected initial outcome: `pass`.
- **R-courtyard**: Mounting-side courtyards do not overlap. Expected initial outcome: `pass`.
- **R-containment**: Every courtyard is wholly inside the board outline. Expected initial outcome: `pass`.
- **R-drc-final**: Native DRC including connectivity and schematic parity has no issues. Expected initial outcome: `defect`.

Native DRC must report unconnected items and no courtyard violation, board-edge violation or parity issue. This is the kicad-route (AGENTEE-93) input; its router exit is recorded, not required to be 0.
