# Physical review experiments — run 606e4450, candidate ebdc7eb9…

## Environment
- pcbnew / KiCad 10.0.7-10.0.7~ubuntu24.04.1, python3 (Ubuntu 24.04)
- Candidate hashes verified against packet:
  rc_filter.kicad_pcb f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de
  rc_filter.kicad_sch ecc56a68a96b9297d8751ae7686a9c8818e9cac84faefafa364b06a0af8a342b

## width_check.py / width_check_raw.txt
Raw S-expression parse of all (segment …) blocks in the saved board.
Result: 5 segments, all width 0.25 mm, all < 0.30 mm requirement.
UUIDs: ae12bf6f (/IN), 69a2edc1 (/OUT), 8b528360 (/OUT), a6dab0b4 (/OUT), 7deb1fb0 (GND).

## width_check_pcbnew.py / width_check_pcbnew.txt
Independent extraction via pcbnew API. Same 5 PCB_TRACK items at 0.25 mm,
min width 0.25 mm; via (GND) 0.6 mm diameter is a via, not a track segment.
Board design setting m_TrackMinWidth = 0.2 mm -> explains why kicad-cli DRC
(which enforces only the board's own rules) reports 0 violations despite the
exercise requirement of 0.30 mm. rc_filter.kicad_pro defines no custom
track_widths/netclass minima.

## connectivity_zone_check.py / connectivity_zone_check.txt
Scratch copy only (candidate untouched). Saved B.Cu GND zone fill: 1 outline,
187 vertices. After ZONE_FILLER refill: identical polygon (sha256 match of
Format output), ratsnest unconnected count 0. Saved fill is not stale;
GND return path J1.3 -> B.Cu zone -> via (113,100) -> C1.2 track is intact.
Note: script printed zone layer via GetLayerName() which returned "F.Cu";
this is an API quirk for zones — file and geometry probe both say B.Cu.

## Limitations
- No fabrication outputs (gerbers) generated; review based on saved board.
- No thermal/ampacity evaluation attempted (explicitly out of scope per
  requirements.txt; requirement is purely geometric).
