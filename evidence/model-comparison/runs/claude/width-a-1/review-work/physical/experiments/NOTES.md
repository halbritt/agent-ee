# Physical review notes — RC low-pass fixture (run bbc5e5e2)
Date: 2026-10-16. Tools: KiCad/kicad-cli 10.0.7, pcbnew Python 10.0.7, Python 3.12.3.

## Inputs verified
sha256 of /candidate/project/rc_filter.kicad_pcb = f8264d95... matches packet board_sha256 (hashes.txt).
sha256 of rc_filter.kicad_sch = ecc56a68... matches packet.

## Scope criterion: every saved copper track segment width >= 0.20 mm
- width_check.py (text parse of native .kicad_pcb, python3 -I): 5 (segment) blocks, 0 arcs,
  all width 0.25 mm, min 0.25 mm -> PASS. Result: width_check_result.json.
- pcbnew_probe.py (pcbnew API): 5 PCB_TRACK segments, min 0.25 mm; 1 via (0.6/0.3, GND),
  unconnected=0. Result: pcbnew_probe_result.json.
- Note: the harness geometry check (work/geometry) extracted tracks but applied no threshold
  (params {}, summary "no declared geometric requirement applied"); the pass/fail against the
  0.20 mm criterion was NOT machine-adjudicated in the packet. Independently closed here.

## Connectivity / current path (saved state)
- Track endpoints coincide exactly with pad centers (J1.1->R1.1 /IN; R1.2->C1.1,
  J1.2->bend->/OUT trunk; C1.2->via GND). zone_conn_result.json.
- GND path: C1.2 -> 0.25 mm F.Cu track -> via(113,100) solidly embedded in B.Cu GND pour
  (containment test true at center and ±0.25 mm ring points) -> pour -> 4 thermal spokes
  (45/135/225/315 deg, bridge width 0.5 mm) into J1.3. No F.Cu GND route exists between
  C1 side and J1.3; the B.Cu pour is the sole GND return. Saved fill provides it.
- Refill staleness test (refill_compare.py, scratch copy /work/experiments/scratch_refill.kicad_pcb;
  candidate untouched): saved fill area 192.287427932463 mm^2 == fresh refill area, 1 outline each,
  delta 0.0 -> saved fill is current, not stale.

## Other physical checks
- Board outline 20 x 12 mm closed rectangle (96,96)-(116,108); all footprints, tracks, via and
  courtyards inside; via at (113,100) is 3 mm from right edge.
- Cross-net spacing: /OUT trunk at y=102.54 clears GND pad J1.3 by ~1.565 mm edge-to-edge;
  vertical /OUT at x=107.5 clears R1.2/C1.1 pads by >0.9 mm. kicad-cli DRC --severity-all
  (packet work/drc) = 0 violations, 0 unconnected, 0 parity issues; consistent with my probes.
- Via 0.6/0.3 (0.15 mm annular ring) is KiCad default geometry; no fab spec supplied, not judged.
- geometry_probe console contains wx PROPERTY_ENUM assert chatter (work/geometry/console.log);
  exit 0, output consistent with my independent extraction -> tool noise, not a candidate issue.

## Limitations
- No fabrication-house-specific DFM rules, no thermal/ampacity evaluation (explicitly out of
  scope per requirements.txt), no mechanical/3D check, mask/paste apertures not separately audited.
