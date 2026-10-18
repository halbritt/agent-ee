# Original RC unplaced: declared fixture requirements

These expectations were authored independently of native/check output.
They characterize deliberately incomplete stage inputs; they do not waive any
acceptance check. A seeded failure is a production defect.

The circuit is J1.1 -- R1 10k -- OUT (J1.2) -- C1 100n -- GND (J1.3).
The original board has a 50 x 30 mm rectangular outline and two copper layers.
No stage input has tracks or vias. Only the two placed variants have an unfilled
B.Cu /GND zone. Unplaced anchors lie on a 10 mm off-board staging row. The placed
anchors are R1=(20,15), C1=(30,15), J1=(5,10) mm. The seed changes only C1 to
(20,16.5) and J1 to (-0.5,10), causing overlap and containment defects.

- **R-erc**: Native ERC has no issues. Expected initial outcome: `pass`.
- **R-netlist**: Native XML exports the three-component RC network. Expected initial outcome: `info`.
- **R-drc-placement**: Native placement violations and schematic parity have no issues; unrouted connectivity is ungraded. Expected initial outcome: `pass`.
- **R-courtyard**: Mounting-side courtyards do not overlap. Expected initial outcome: `pass`.
- **R-containment**: Every courtyard is wholly inside the board outline. Expected initial outcome: `defect`.
- **R-drc-final**: Native DRC including connectivity and schematic parity has no issues. Expected initial outcome: `defect`.

Native DRC must report unconnected items for each variant. Placed-unrouted must
have no courtyard violations or parity issues. Seeded-placement must report
`courtyards_overlap` citing R1 and C1. Board-edge issues from J1 are expected.
