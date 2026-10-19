# AGENTEE-219 analyzer false-positive reproducer

`generate.py` authors every symbol, pin list, net name and coordinate in
`reproducer.kicad_sch` from scratch; no external design, stock library or
footprint bytes are copied. `LSM6DSO32` is a part-number fact used only so the
analyzer's I2C keyword classifier engages as it did on the real pod; no device
qualification is claimed. Regenerate with system Python (no pcbnew needed):

```sh
python3 fixtures/kicad-happy/ac4-false-positives/generate.py
```

The severity-gate test pairs this schematic with the owned
`severity-gate/warning/gate.kicad_pcb` board only because the gate requires a
board; only schematic-analyzer error identities are asserted.

Independent expectations (`expected.json` records the exact finding IDs):

| Part | Mechanism | Pinned 2.3.1 | Proposed correction |
| --- | --- | --- | --- |
| U2 | `U1.SW` (power_out) → L1 → `MID_NODE` → U2 `IN` | PP-001 error (false positive) | clear |
| U4 | `U3.OUT` (power_out) on `REF_BUF` → U4 `VREF` | PP-001 error (false positive) | clear |
| U5 | J1 → Q1 P-MOSFET, gate pulled to GND by R1, zener D1 → `PROT_NODE` | PP-001 error (false positive) | clear |
| U7 | `LSM6DSO32` CS driven by `U6.IO3`; SDA/SCL on `SPI_MOSI`/`SPI_SCK` | 2 × PR-001 error (false positive) | clear; one PR-001 `info` records the SPI classification |
| U8 | supply only through C4/C5 from `+12V` | PP-001 error (real) | still error |
| U10 | Q2 P-MOSFET gate driven by `U6.IO6` (conduction not proven) | PP-001 error (boundary) | still error |
| U9 | `LSM6DSO32` CS tied to `+3V3` (I2C mode), no pull-ups | 2 × PR-001 error (real) | still error |

The pinned findings are recorded known analyzer limitations, not a pass: the
severity gate reports them as errors, and a disposition naming them waives
nothing. The correction is a proposal at an exact kicad-happy commit; adopting it
is a pin change that needs requalification.
