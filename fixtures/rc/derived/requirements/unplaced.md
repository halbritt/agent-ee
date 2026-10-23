# Control-derived RC unplaced: declared fixture requirements

These expectations were authored before any check ran on the derived boards,
independently of native/check output. They characterize a deliberately incomplete
stage input; they do not waive any acceptance check. A seeded failure is a
production defect.

The board is derived from `fixtures/rc/control` (origin T1) by
`fixtures/rc/derived/generate.py` through pcbnew. The circuit is
J1.1 (/IN) -- R1 10k -- /OUT (J1.2) -- C1 100n -- GND (J1.3).

Saved boundary: R1, C1 and J1 on a 10 mm staging row at x = 125, 135, 145 mm (y = 100 mm), 9 mm or more right of the 96..116 x 96..108 mm outline. Every control track, the GND via and the B.Cu GND zone are removed: no copper outside the footprints.

- **R-erc**: Native ERC has no issues. Expected initial outcome: `pass`.
- **R-netlist**: Native XML exports the three-component RC network. Expected initial outcome: `info`.
- **R-drc-placement**: Native placement violations and schematic parity have no issues; unrouted connectivity is ungraded. Expected initial outcome: `defect`.
- **R-courtyard**: Mounting-side courtyards do not overlap. Expected initial outcome: `pass`.
- **R-containment**: Every courtyard is wholly inside the board outline. Expected initial outcome: `defect`.
- **R-drc-final**: Native DRC including connectivity and schematic parity has no issues. Expected initial outcome: `defect`.

Native DRC must report unconnected items. Off-board footprints are a containment defect; no courtyard overlap is expected.
