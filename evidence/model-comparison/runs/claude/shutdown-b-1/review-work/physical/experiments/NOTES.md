# Physical review experiments — rc_filter shutdown fixture
Run: bd7c42ca-0fce-4b4b-a5c5-57c86784e853
Date: 2026-10-16. Tools: KiCad/kicad-cli 10.0.7, pcbnew python 10.0.7, ngspice-42, python3.

## Input integrity
sha256 of /candidate/project/rc_filter.kicad_sch = ac819f42... and
rc_filter.kicad_pcb = 69ab00b2... match the packet artifact hashes.

## 1. discharge_analytic.py / discharge_analytic.out
Analytic RC discharge with saved values (R1=100k, C1=100n from schematic and
netlist). tau = 10 ms. V(/OUT, 5 ms) = 0.6065 V vs requirement <= 0.01 V
(fails by 60.7x). To meet the spec with C=100n, R1 <= 10.857 kOhm
(tau <= 1.0857 ms = 5 ms / ln(100)). Saved values reach 0.01 V at ~46 ms.

## 2. shutdown.cir / shutdown_ngspice.log / shutdown_tran.dat
ngspice -b transient, ic=1 V on C1, /IN tied to 0 V, .tran 10u 6m uic.
meas: vout_at_5ms = 6.065306e-01 V. Confirms analytic result.
Assumption: ideal passives per scope; no load (per requirements.txt).

## 3. board_connectivity.py / board_connectivity.out
pcbnew probe of the saved board (fill as saved, no refill):
- ratsnest unconnected count = 0
- pad nets/positions match work/geometry/geometry.json
- GND zone (B.Cu) filled; J1.3 PTH pad and the 0.6/0.3 via at (113,100)
  both geometrically collide with the saved B.Cu filled polygon
  (thermal-relief spokes present), so the C1.2 -> via -> B.Cu plane ->
  J1.3 GND return is continuous as saved.
Note: pcbnew prints harmless PROPERTY_ENUM asserts on stderr (library quirk,
also present in packet work/geometry/console.log); exit code 0.

## 4. erc_rerun.json / drc_rerun.json
Independent kicad-cli ERC and DRC (severity-all, schematic parity):
0 violations, 0 unconnected, 0 parity issues — matches packet checks.

## Limitations
- Zone evaluated as saved; review rules forbid refill of the read-only candidate.
- No tolerance/parasitic/thermal/ampacity analysis: scope mandates ideal
  passives and forbids imposing unstated ratings.
