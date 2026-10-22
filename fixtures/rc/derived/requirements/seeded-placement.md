# Control-derived RC seeded-placement: declared fixture requirements

These expectations were authored before any check ran on the derived boards,
independently of native/check output. They characterize a deliberately incomplete
stage input; they do not waive any acceptance check. A seeded failure is a
production defect.

The board is derived from `fixtures/rc/control` (origin T1) by
`fixtures/rc/derived/generate.py` through pcbnew. The circuit is
J1.1 (/IN) -- R1 10k -- /OUT (J1.2) -- C1 100n -- GND (J1.3).

Saved boundary: The placed-unrouted board with exactly two seeded moves: C1 to (108,100) mm, so its courtyard overlaps R1's, and J1 to (95.5,100) mm, so its pins lie 0.5 mm outside the left board edge.

- **R-erc**: Native ERC has no issues. Expected initial outcome: `pass`.
- **R-netlist**: Native XML exports the three-component RC network. Expected initial outcome: `info`.
- **R-drc-placement**: Native placement violations and schematic parity have no issues; unrouted connectivity is ungraded. Expected initial outcome: `defect`.
- **R-courtyard**: Mounting-side courtyards do not overlap. Expected initial outcome: `defect`.
- **R-containment**: Every courtyard is wholly inside the board outline. Expected initial outcome: `defect`.
- **R-drc-final**: Native DRC including connectivity and schematic parity has no issues. Expected initial outcome: `defect`.

Native DRC must report `courtyards_overlap` citing R1 and C1. Board-edge and containment issues from J1 are expected.
