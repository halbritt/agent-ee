# Matched benchmark against stock KRT — 2026-11-09 (AGENTEE-628)

Status: **preregistration**. This section was written and committed before any
benchmark run. Results are appended below it; the preregistered text is not
edited after the first run except to mark deviations, each labelled as one.

The question: on real boards, is agent-ee's placement and routing better than
stock upstream KiCadRoutingTools (KRT)? This has not been measured before. A
loss or a refusal is a valid result and is reported as it happened.

## Preregistered board set

Candidate pool (inventoried with `benchmarks/krt-matched/inventory.py`, which
only counts objects; no tool under test was run on any candidate):

- every `kicad_files/*.kicad_pcb` in upstream KRT at the benchmarked commit;
- every `demos/*/*.kicad_pcb` in the KiCad source repository
  (`gitlab.com/kicad/code/kicad`, master);
- `tinyfpga/TinyFPGA-BX` `board/TinyFPGA-BX.kicad_pcb`,
  `OLIMEX/ESP32-DevKit-LiPo` `HARDWARE/ESP32-DevKit-LiPo-Rev.D/`, and
  `icebreaker-fpga/icebreaker` (GitHub, default branches).

Selection rule, applied to the inventory:

1. Eligible: loads in KiCad 10.0.8 `pcbnew`; 20–100 footprints; 2–4 copper
   layers; a real design (KRT's `qfn_*`, `cap_chain`, `lvds_*` and
   `haasoscope_pro_max_test`/`routed_output` test articles are not).
2. One board per design: where the same design appears twice (KRT's
   `flat_hierarchy` is KiCad's `pic_programmer`; `sonde_u` is `sonde xilinx`;
   `interf_u*` is `interf_u`), it counts once, in the KRT class, because KRT
   develops against it.
3. KRT class (boards in KRT's own repository): sort eligible boards by
   footprint count and take the smallest, the median and the largest.
4. Independent class (not in KRT's repository): take every eligible board.

Inventory of eligible boards (footprints / copper layers / pour zones / track
items in the source):

| Class | Board | Footprints | Layers | Pours | Source tracks |
| --- | --- | ---: | ---: | ---: | ---: |
| KRT | esp_prog | 21 | 2 | 0 | 0 |
| KRT | interf_u_unrouted | 25 | 2 | 0 | 0 |
| KRT | sonde_u | 25 | 2 | 1 | 0 |
| KRT | flat_hierarchy | 64 | 2 | 1 | 0 |
| KRT | splitflap_driver | 65 | 2 | 0 | 0 |
| KRT | watchy | 86 | 4 | 0 | 0 |
| KRT | tigard | 92 | 4 | 0 | 0 |
| independent | ESP32-DevKit-LiPo Rev.D (OLIMEX) | 56 | 2 | 3 | 1329 |
| independent | complex_hierarchy (KiCad demo) | 68 | 2 | 166 | 364 |
| independent | StickHub (KiCad demo) | 94 | 2 | 5 | 1380 |

Not eligible: TinyFPGA-BX (pcbnew 10.0.8 does not load the file); icebreaker
(the clone holds no `.kicad_pcb`); ecc83 (15 footprints); multichannel_mixer
(114); glasgow_revC, kit-dev-coldfire, orangecrab, ulx3s, cm5_minima,
openair-max, tiny_tapeout, video, royalblue54L, jetson, vme-wren (size or layer
count); microwave, constraints (not a netted design / does not load).

**Chosen set (6 boards):**

| # | Board | Class | Why |
| --- | --- | --- | --- |
| 1 | esp_prog | KRT | smallest eligible |
| 2 | flat_hierarchy | KRT | median eligible (4th of 7) |
| 3 | tigard | KRT | largest eligible; also KRT's own placement example |
| 4 | ESP32-DevKit-LiPo Rev.D | independent | all eligible |
| 5 | complex_hierarchy | independent | all eligible |
| 6 | StickHub | independent | all eligible |

No stretch board (~200 parts) is planned in the 3 hour budget. `sonde_u` is
not in the set and is the only board used to debug the harness.

## Preregistered protocol

One generic rule per step, applied identically to every board. No board,
rule, limit or per-board setting is tuned. A tool refusal or crash is the
recorded result for that cell.

**Normalization.** The source board (with its `.kicad_pro`/`.kicad_dru` when
present) is loaded and saved once with pcbnew 10.0.8, so every tool reads the
same current-format file and a project file exists. The saved `.kicad_pro` and
`.kicad_dru` are "the board's own rules" and are restored byte-for-byte beside
every output before scoring.

**Routing arm (same frozen placement).** Input: the normalized board with all
tracks and vias removed, every footprint locked, and zones, rule areas and
rules kept. Both tools get this same file.

- KRT: `python3 py_router/route.py IN OUT` — the README's "Route all nets
  (default)", no other option.
- agent-ee: `agent-ee place run CONFIG --out DIR`, the documented
  placement-to-route entry point, which keeps a fully fixed board's placement.
  If agent-ee refuses the board, the refusal is the result. A labelled
  secondary measurement then runs `kicad-route run` directly on the same
  input; it is not an agent-ee result.

**Placement arm.** Input: the routing-arm board with locks reset by one rule,
then unplaced. Locked: footprints the source design locks, footprints with no
netted pad, and footprints whose reference prefix is `J`, `P`, `CN`, `CON`,
`USB`, `H`, `MH` or `SW` (connectors, mounting holes, switches — the parts
KRT's placement quick start locks). All other footprints are piled off the
board with KRT's documented `make_unplaced.py --keep-locked`; both placers get
that same file.

- Groups (both tools, same file): one group per schematic sheet (the
  footprint's sheet name); footprints without a sheet join one group; the
  anchor is the member with the most pads (ties: reference order). Only
  `name`, `anchor` and `refs` are written.
- KRT: `check_floorplan.py --emit-intent`, then `place_simple.py` with its
  default seed portfolio; the kept seed is the first one named in the
  `judge.json` it writes (its own ranking), without the optional judging route.
- agent-ee: `agent-ee place run` with a minimal intent (schema line and a
  `fixed_poses` row for every locked footprint) and the search settings its own
  design compiler writes by default (4 clustered seeds, 100,000 steps per
  seed, shortlist 4). The placement taken is the one the job selects.
- Both placements are then stripped of any copper and routed with the same
  stock KRT `route.py` at defaults, so only the placement differs. agent-ee's
  own routed result from the same job is reported beside it.

**agent-ee configuration.** Generated by `bench.py` from the board alone:
input paths and hashes, the groups file above, the minimal intent, the router
checkout agent-ee's documented environment provides, and the budget. No
`router.*` option is set.

**Zone fallback (decided now).** agent-ee documents that it refuses boards
with copper pours. If it refuses a board for that reason, the primary cell
records the refusal, and one secondary variant of that board with all pour
zones removed is run through both tools in both arms and reported in a
separate, labelled table.

**Budget.** 900 s wall clock per routing run and per placement run (agent-ee
`budget_s: 900`, whole job; KRT killed at 900 s and scored on what it saved).
At most two heavy jobs at once. Overall target about 3 hours; if it runs out,
remaining cells are reported as not run.

**Scoring (identical for every output).** `kicad-cli pcb drc --format json
--severity-all --refill-zones` on the saved board beside the board's own
rules: unconnected items, DRC errors by type, warning count; via count and
total track length read with pcbnew; wall-clock seconds; exit status and any
refusal text. One run per cell; placement seeds are each tool's own default
portfolio. Per metric the winner is the lower value; the order of importance
is unconnected items, DRC errors, vias, track length, time.

---

Everything below was written after the runs. Run window: 2026-11-09 20:25 to
22:06 UTC on host proximal. Raw results:
`~/.local/share/agent-ee/krt-matched-20261109/` (not committed).

## Result in brief

- **agent-ee's `place run` accepted one of the six preregistered boards**
  (esp_prog). It refused the other five in both arms, in about 2 seconds
  each: four for copper pours, and tigard because the placement engine's
  contour-vertex cap rejects one of its connector pads. With the pours removed
  (the preregistered fallback) it still refused all four: three for a
  non-rectangular outline and StickHub for a 45 degree part. Stock KRT
  produced a routed board in every cell, although its placer found no legal
  placement on ESP32-DevKit-LiPo and did not finish its seeds on StickHub.
- **Routing arm, esp_prog (the only matched pair):** both tools connected
  every net. agent-ee's route has fewer errors against the board's own rules
  (10 against 70) and less track (293.8 mm against 310.4 mm); stock KRT has
  fewer vias (24 against 31) and is faster (6.5 s against 39 s). agent-ee's
  9 new errors include a short and six hole-clearance errors, and agent-ee
  itself rejected the result (`verification_failed`, `INELIGIBLE`, exit 1).
  KRT's 69 new errors are almost all track, via and clearance values below
  the board's limits, which KRT lowers in the project file it writes; under
  that rewritten project its board has 5 errors.
- **Placement arm, esp_prog (the only matched pair):** routed by the same
  stock KRT router, KRT's placement needed 16 vias and 228.9 mm of track with
  12 errors; agent-ee's needed 31 vias and 336.1 mm with 15 errors. Both
  connected every net. KRT placed in 57 s (12 seeds); agent-ee's job took
  834 s. Across KRT's twelve seeds the routed result ranged from 15 to 33 vias and 228.94 to 332.69 mm; agent-ee's 4 placements (three seed sets and a second search setting) all needed more than KRT's median: they ranged from 31 to 37 vias and 314.04 to 342.71 mm.
- KRT's own placer did not do well on the independent boards either: routed
  by the same router, its placements left 23 nets open on ESP32-DevKit-LiPo
  and 21 on StickHub, where the designers' placements left 4 and 0.
- On this evidence agent-ee is **not better than stock KRT**: it lost the
  placement comparison on vias, track length and time on the one board it
  accepted, split the routing comparison there, and could not be compared at all on five of six boards
  because it refused them.

## Deviations from the preregistration

1. **Teardrops.** "Tracks and vias removed" also removes teardrop zones.
   complex_hierarchy carries 165; they are copper that belongs to tracks, and
   KRT's `make_unplaced.py` refused the board with them (`carries routed
   copper`). Decided during harness debugging, before any run on a set board.
2. **Empty reference in the intent.** The first esp_prog routing run was
   refused with `intent.fixed_poses[0].ref: expected nonempty text`: the board
   has a footprint with an empty reference, and the preregistered intent rule
   wrote a row for it. The rule now skips unnamed footprints (their native
   lock still fixes them). All agent-ee results here use the corrected rule;
   the refused run is kept under `superseded/`.
3. **agent-ee runs in the CI image.** `agent-ee doctor --router-checkout`
   reports problems for every kicad-route checkout on the host (see defects),
   so `place run` was run with `ae-ci-exec`, where `/opt/kicad-route`
   qualifies. KRT ran on the host. Both use KiCad 10.0.8; all scoring was
   done on the host.
4. **KRT placement on poured boards.** For the four boards agent-ee refused
   for pours, the primary placement cell was first run for agent-ee only (to
   record the refusal). KRT's primary placement cells were run afterwards
   and have no agent-ee counterpart.
5. **KRT placer concurrency and seed choice.** `place_simple.py` ran with
   `--jobs 1` (its default is up to 4) to respect the two-job limit. The kept
   seed is the first in `judge.json`; when the placer named no seed (all
   seeds illegal) or the 900 s budget ended first, the lowest-cost completed
   seed was kept. Those cells are marked in the placer notes.
6. **Additions, all labelled:** a score of the unrouted input (to separate
   pre-existing from new errors); a second score of KRT outputs under the
   project file KRT itself wrote; the seed spread on esp_prog; a second
   agent-ee search setting (the one documented in
   `placement-job-2026-10-24.md`: 12 seeds, 10,000 steps, shortlist 6); and
   agent-ee runs on the debug board `sonde_u`, which is outside the set.

## Versions

| Item | Version |
| --- | --- |
| agent-ee | `74df8d77` (main) plus this harness; `src/` unchanged |
| agent-ee CI image | `sha256:ee60b8081162…` (`agent-ee:exec-9e5f73d947e3f006`), Python 3.12.3, KiCad 10.0.8 |
| Placement engine | `agent-ee-placement 0.1.0`, image binary sha256 `ad173dc65126…` (crate last changed at `a6712f5b`) |
| kicad-route (as agent-ee uses it) | `995891ae8852f6524815d5f06ad3a062f40ed8e0`, the image's `/opt/kicad-route`; builtin backend "0.23.1", owned build `f75fa1cb57fd`, upstream base `295f9ee1` |
| Stock KRT | `e6bc31e2291a91327217cd668885e065a487ec92` (`v0.23.8-268-ge6bc31e2`), fresh clone, own venv (Python 3.14.4, numpy 2.5.4, scipy 1.18.1, shapely 2.2.0, ortools 9.15.6755) |
| KRT Rust router | prebuilt `grid_router v0.23.7` from release v0.23.8, as `build_router.py` installs by default |
| KiCad (host, scoring) | `kicad-cli` 10.0.8, pcbnew 10.0.8 |
| Board sources | KRT clone above; KiCad `f87615475ec8b349c979b22474eb1c3d2d284f65`; OLIMEX ESP32-DevKit-LiPo `1ebbbb5ceaa84b1d67631d8d542d7f6128c19fc1` |

Budget used: 900 s per routing run and per placement run. At most two jobs
at a time.

## How to read the tables

"DRC errors" is native `kicad-cli pcb drc` against the board's own rules: the
project file of the normalized source. esp_prog and tigard ship without a
project file, so their rules are KiCad's defaults plus what the board file
holds. "Errors, KRT-written rules" rescores a KRT output beside the project
file KRT wrote next to it; KRT lowers minimum track, via and clearance values
there to what it routed. The "input (unrouted)" row shows what the board
carried before any tool ran; subtract it to get new errors. "Seconds" is the
tool's wall clock (for agent-ee the job's own `elapsed_s`, without container
start). In the placement tables the seconds of a "→ KRT route" row are the
routing time; placement times are in the placer notes and the spread table.
Warning counts are in the raw results. Times were taken with two jobs
running and vary with load.

### Routing arm, primary (pours kept)

| Board | Tool | Unconnected | DRC errors | Errors, KRT-written rules | Vias | Track mm | Error types (board rules) | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| esp_prog | input (unrouted) | 46 | 1 |  | 0 | 0.0 | clearance 1 |  |
| esp_prog | stock KRT | 0 | 70 | 5 | 24 | 310.36 | annular_width 2, clearance 15, drill_out_of_range 6, track_width 43, via_diameter 4 | 6 |
| esp_prog | agent-ee `place run` | 0 | 10 |  | 31 | 293.82 | clearance 3, hole_clearance 6, shorting_items 1 | 39 |
| flat_hierarchy | input (unrouted) | 87 | 2 |  | 0 | 0.0 | clearance 2 |  |
| flat_hierarchy | stock KRT | 0 | 2 | 2 | 39 | 1641.68 | clearance 2 | 23 |
| flat_hierarchy | agent-ee `place run` | refused |  |  |  |  |  | 2 |
| flat_hierarchy | kicad-route direct (secondary) | router exit 1 |  |  |  |  |  | 13 |
| tigard | input (unrouted) | 290 | 48 |  | 0 | 0.0 | clearance 25, copper_edge_clearance 2, courtyards_overlap 2, drill_out_of_range 16, shorting_items 1, solder_mask_bridge 2 |  |
| tigard | stock KRT | 0 | 594 | 8 | 168 | 1785.78 | annular_width 52, clearance 204, copper_edge_clearance 2, courtyards_overlap 2, drill_out_of_range 71, hole_clearance 6, shorting_items 1, solder_mask_bridge 2, track_width 199, via_diameter 55 | 104 |
| tigard | agent-ee `place run` | refused |  |  |  |  |  | 2 |
| tigard | kicad-route direct (secondary) | 17 | 48 |  | 133 | 1691.64 | clearance 25, copper_edge_clearance 2, courtyards_overlap 2, drill_out_of_range 16, shorting_items 1, solder_mask_bridge 2 | 127 |
| esp32_devkit_lipo | input (unrouted) | 108 | 43 |  | 0 | 0.0 | copper_edge_clearance 38, starved_thermal 4, zones_intersect 1 |  |
| esp32_devkit_lipo | as designed (reference) | 0 | 74 |  | 108 | 1630.77 | copper_edge_clearance 69, hole_clearance 2, starved_thermal 2, zones_intersect 1 |  |
| esp32_devkit_lipo | stock KRT | 4 | 746 | 48 | 175 | 1250.7 | annular_width 33, clearance 336, copper_edge_clearance 38, drill_out_of_range 62, hole_clearance 10, items_not_allowed 1, starved_thermal 4, track_width 199, via_diameter 62, zones_intersect 1 | 143 |
| esp32_devkit_lipo | agent-ee `place run` | refused |  |  |  |  |  | 2 |
| esp32_devkit_lipo | kicad-route direct (secondary) | 21 | 49 |  | 133 | 1013.22 | copper_edge_clearance 38, items_not_allowed 1, starved_thermal 6, track_width 3, zones_intersect 1 | 133 |
| complex_hierarchy | input (unrouted) | 87 | 0 |  | 0 | 0.0 | - |  |
| complex_hierarchy | as designed (reference) | 0 | 0 |  | 0 | 1265.69 | - |  |
| complex_hierarchy | stock KRT | 0 | 0 | 0 | 14 | 1186.61 | - | 12 |
| complex_hierarchy | agent-ee `place run` | refused |  |  |  |  |  | 2 |
| complex_hierarchy | kicad-route direct (secondary) | router exit 1 |  |  |  |  |  | 12 |
| stickhub | input (unrouted) | 128 | 0 |  | 0 | 0.0 | - |  |
| stickhub | as designed (reference) | 0 | 0 |  | 87 | 742.67 | - |  |
| stickhub | stock KRT | 0 | 43 | 30 | 83 | 569.83 | annular_width 3, drill_out_of_range 2, solder_mask_bridge 31, track_width 4, via_diameter 3 | 32 |
| stickhub | agent-ee `place run` | refused |  |  |  |  |  | 4 |
| stickhub | kicad-route direct (secondary) | 4 | 32 |  | 90 | 594.67 | solder_mask_bridge 32 | 66 |

Winner per metric, esp_prog (the only board both tools routed):

| Metric | stock KRT | agent-ee | Winner |
| --- | ---: | ---: | --- |
| Unconnected | 0 | 0 | tie |
| DRC errors, board rules (new) | 70 (69) | 10 (9) | agent-ee |
| Vias | 24 | 31 | KRT |
| Track length mm | 310.36 | 293.82 | agent-ee |
| Seconds | 6.5 | 39.4 | KRT |
| Tool's own verdict | exit 0 | exit 1, `verification_failed` | KRT |

On the other five boards KRT has a result and agent-ee has none. KRT left
nets open only on ESP32-DevKit-LiPo (4). The secondary rows (kicad-route run
directly with the adapter's base flags, not an agent-ee result) show the
owned router adds few or no errors against the board's rules but leaves nets
open where stock KRT does not (tigard 17, ESP32-DevKit-LiPo 21, StickHub 4),
and crashes on flat_hierarchy and complex_hierarchy.

### Routing arm, zone fallback (pours removed)

| Board | Tool | Unconnected | DRC errors | Errors, KRT-written rules | Vias | Track mm | Error types (board rules) | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flat_hierarchy | input (unrouted) | 127 | 2 |  | 0 | 0.0 | clearance 2 |  |
| flat_hierarchy | stock KRT | 0 | 2 | 2 | 44 | 2011.48 | clearance 2 | 12 |
| flat_hierarchy | agent-ee `place run` | refused |  |  |  |  |  | 2 |
| flat_hierarchy | kicad-route direct (secondary) | router exit 1 |  |  |  |  |  | 12 |
| esp32_devkit_lipo | input (unrouted) | 137 | 38 |  | 0 | 0.0 | copper_edge_clearance 38 |  |
| esp32_devkit_lipo | stock KRT | 3 | 392 | 39 | 141 | 1238.45 | annular_width 5, clearance 116, copper_edge_clearance 38, drill_out_of_range 29, hole_clearance 1, items_not_allowed 1, track_width 173, via_diameter 29 | 93 |
| esp32_devkit_lipo | agent-ee `place run` | refused |  |  |  |  |  | 1 |
| esp32_devkit_lipo | kicad-route direct (secondary) | 14 | 38 |  | 101 | 1088.54 | copper_edge_clearance 38 | 57 |
| complex_hierarchy | input (unrouted) | 112 | 0 |  | 0 | 0.0 | - |  |
| complex_hierarchy | stock KRT | 0 | 0 | 0 | 15 | 1423.87 | - | 8 |
| complex_hierarchy | agent-ee `place run` | refused |  |  |  |  |  | 2 |
| complex_hierarchy | kicad-route direct (secondary) | router exit 1 |  |  |  |  |  | 11 |
| stickhub | input (unrouted) | 226 | 0 |  | 0 | 0.0 | - |  |
| stickhub | stock KRT | 1 | 115 | 35 | 75 | 734.03 | annular_width 12, clearance 4, drill_out_of_range 14, solder_mask_bridge 26, track_width 43, via_diameter 16 | 51 |
| stickhub | agent-ee `place run` | refused |  |  |  |  |  | 4 |
| stickhub | kicad-route direct (secondary) | 6 | 9 |  | 67 | 688.1 | solder_mask_bridge 9 | 60 |

### Placement arm, primary (pours kept)

| Board | Tool | Unconnected | DRC errors | Errors, KRT-written rules | Vias | Track mm | Error types (board rules) | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| esp_prog | KRT placer → KRT route | 0 | 12 | 4 | 16 | 228.94 | annular_width 1, clearance 3, drill_out_of_range 3, shorting_items 1, track_width 1, via_diameter 3 | 2 |
| esp_prog | agent-ee placer → KRT route | 0 | 15 | 3 | 31 | 336.14 | annular_width 1, clearance 3, drill_out_of_range 2, track_width 7, via_diameter 2 | 4 |
| esp_prog | agent-ee placer → agent-ee route (own) | 1 | 15 |  | 30 | 293.23 | clearance 2, hole_clearance 13 | 834 |
| flat_hierarchy | KRT placer → KRT route | 0 | 2 | 2 | 36 | 1271.05 | clearance 2 | 19 |
| flat_hierarchy | agent-ee placer → KRT route | no placement |  |  |  |  |  |  |
| flat_hierarchy | agent-ee placer → agent-ee route (own) | refused |  |  |  |  |  | 2 |
| tigard | KRT placer → KRT route | 1 | 861 | 8 | 192 | 2146.6 | annular_width 36, clearance 499, copper_edge_clearance 2, courtyards_overlap 2, drill_out_of_range 58, hole_clearance 20, shorting_items 1, solder_mask_bridge 2, track_width 199, via_diameter 42 | 176 |
| tigard | agent-ee placer → KRT route | no placement |  |  |  |  |  |  |
| tigard | agent-ee placer → agent-ee route (own) | refused |  |  |  |  |  | 2 |
| esp32_devkit_lipo | KRT placer → KRT route | 23 | 402 | 48 | 111 | 838.25 | annular_width 16, clearance 115, copper_edge_clearance 3, drill_out_of_range 24, hole_clearance 3, shorting_items 20, solder_mask_bridge 20, starved_thermal 2, track_width 174, via_diameter 24, zones_intersect 1 | 165 |
| esp32_devkit_lipo | agent-ee placer → KRT route | no placement |  |  |  |  |  |  |
| esp32_devkit_lipo | agent-ee placer → agent-ee route (own) | refused |  |  |  |  |  | 1 |
| complex_hierarchy | KRT placer → KRT route | 0 | 74 | 74 | 17 | 948.4 | silk_over_copper 25, silk_overlap 49 | 9 |
| complex_hierarchy | agent-ee placer → KRT route | no placement |  |  |  |  |  |  |
| complex_hierarchy | agent-ee placer → agent-ee route (own) | refused |  |  |  |  |  | 1 |
| stickhub | KRT placer → KRT route | 21 | 387 | 55 | 133 | 685.73 | annular_width 34, clearance 27, drill_out_of_range 31, solder_mask_bridge 57, track_width 199, via_diameter 39 | 289 |
| stickhub | agent-ee placer → KRT route | no placement |  |  |  |  |  |  |
| stickhub | agent-ee placer → agent-ee route (own) | refused |  |  |  |  |  | 4 |

Winner per metric, esp_prog, both placements routed by stock KRT:

| Metric | KRT placer | agent-ee placer | Winner |
| --- | ---: | ---: | --- |
| Unconnected | 0 | 0 | tie |
| DRC errors, board rules | 12 | 15 | KRT |
| DRC errors, KRT-written rules | 4 | 3 | agent-ee |
| Vias | 16 | 31 | KRT |
| Track length mm | 228.94 | 336.14 | KRT |
| Placement seconds | 56.5 | 834.0 (whole job) | KRT |

agent-ee's own route of its own placement (same job) left 1 net open with 15
errors, and the job ended `verification_failed`.

### Placement arm, zone fallback (pours removed)

| Board | Tool | Unconnected | DRC errors | Errors, KRT-written rules | Vias | Track mm | Error types (board rules) | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flat_hierarchy | KRT placer → KRT route | 0 | 2 | 2 | 36 | 1771.8 | clearance 2 | 13 |
| flat_hierarchy | agent-ee placer → KRT route | no placement |  |  |  |  |  |  |
| flat_hierarchy | agent-ee placer → agent-ee route (own) | refused |  |  |  |  |  | 2 |
| esp32_devkit_lipo | KRT placer → KRT route | 18 | 363 | 43 | 92 | 953.3 | annular_width 2, clearance 117, copper_edge_clearance 3, drill_out_of_range 17, hole_clearance 2, shorting_items 20, solder_mask_bridge 20, track_width 165, via_diameter 17 | 63 |
| esp32_devkit_lipo | agent-ee placer → KRT route | no placement |  |  |  |  |  |  |
| esp32_devkit_lipo | agent-ee placer → agent-ee route (own) | refused |  |  |  |  |  | 2 |
| complex_hierarchy | KRT placer → KRT route | 0 | 74 | 74 | 16 | 1267.67 | silk_over_copper 25, silk_overlap 49 | 7 |
| complex_hierarchy | agent-ee placer → KRT route | no placement |  |  |  |  |  |  |
| complex_hierarchy | agent-ee placer → agent-ee route (own) | refused |  |  |  |  |  | 1 |
| stickhub | KRT placer → KRT route | 26 | 228 | 38 | 115 | 786.26 | annular_width 21, clearance 23, diff_pair_gap_out_of_range 1, drill_out_of_range 18, solder_mask_bridge 38, track_width 103, via_diameter 24 | 245 |
| stickhub | agent-ee placer → KRT route | no placement |  |  |  |  |  |  |
| stickhub | agent-ee placer → agent-ee route (own) | refused |  |  |  |  |  | 4 |

### Seed spread on esp_prog (placement arm)

Every placement below was routed by stock KRT at defaults.

| Placement | Unconnected | DRC errors | Errors, KRT-written rules | Vias | Track mm | Placement seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| KRT seed 0 | 0 | 18 | 3 | 33 | 332.69 | 56.5 (all 12 seeds) |
| KRT seed 1 | 0 | 27 | 3 | 27 | 270.26 | 56.5 (all 12 seeds) |
| KRT seed 2 | 0 | 9 | 3 | 22 | 240.4 | 56.5 (all 12 seeds) |
| KRT seed 3 | 0 | 8 | 6 | 15 | 255.69 | 56.5 (all 12 seeds) |
| KRT seed 4 | 0 | 13 | 4 | 28 | 314.93 | 56.5 (all 12 seeds) |
| KRT seed 5 | 0 | 11 | 2 | 23 | 285.51 | 56.5 (all 12 seeds) |
| KRT seed 6 | 0 | 14 | 2 | 22 | 289.51 | 56.5 (all 12 seeds) |
| KRT seed 7 | 0 | 8 | 3 | 23 | 250.26 | 56.5 (all 12 seeds) |
| KRT seed 8 | 0 | 20 | 2 | 27 | 299.94 | 56.5 (all 12 seeds) |
| KRT seed 9 (kept by KRT ranking) | 0 | 12 | 4 | 16 | 228.94 | 56.5 (all 12 seeds) |
| KRT seed 10 | 0 | 8 | 3 | 20 | 239.58 | 56.5 (all 12 seeds) |
| KRT seed 11 | 0 | 55 | 4 | 22 | 300.18 | 56.5 (all 12 seeds) |
| agent-ee, compiler default, seeds 0-3 | 0 | 15 | 3 | 31 | 336.14 | 834.046 (whole job, verification_failed) |
| agent-ee, compiler default, seeds 4-7 | 0 | 100 | 4 | 31 | 314.04 | 900.002 (whole job, deadline) |
| agent-ee, compiler default, seeds 8-11 | 0 | 24 | 2 | 31 | 331.59 | 882.435 (whole job, verification_failed) |
| agent-ee, 12 seeds x 10,000 steps | 0 | 21 | 3 | 37 | 342.71 | 793.151 (whole job, verification_failed) |

KRT's twelve seeds: vias 15 to 33 (median 22.5), track 228.94 to 332.69 mm (median 277.9). agent-ee's 4 placements: vias 31 to 37, track 314.04 to 342.71 mm.

### KRT placer notes

| Board | Variant | Seconds | Exit | Seeds written | Kept seed | Kept by | Placement-only DRC errors |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| esp_prog | primary | 56.5 | 0 | 12 | 9 | judge.json first | 1 (clearance 1) |
| flat_hierarchy | primary | 239.3 | 0 | 12 | 10 | judge.json first | 2 (clearance 2) |
| flat_hierarchy | pours removed | 421.9 | 0 | 12 | 10 | judge.json first | 2 (clearance 2) |
| tigard | primary | 822.4 | 0 | 12 | 5 | judge.json first | 48 (clearance 25, copper_edge_clearance 2, courtyards_overlap 2, drill_out_of_range 16, shorting_items 1, solder_mask_bridge 2) |
| esp32_devkit_lipo | primary | 191.4 | 4 | 12 | 9 | lowest cost; all seeds illegal | 49 (clearance 1, copper_edge_clearance 3, shorting_items 20, solder_mask_bridge 20, starved_thermal 4, zones_intersect 1) |
| esp32_devkit_lipo | pours removed | 179.3 | 4 | 12 | 9 | lowest cost; all seeds illegal | 44 (clearance 1, copper_edge_clearance 3, shorting_items 20, solder_mask_bridge 20) |
| complex_hierarchy | primary | 166.1 | 0 | 12 | 7 | judge.json first | 74 (silk_over_copper 25, silk_overlap 49) |
| complex_hierarchy | pours removed | 229.6 | 0 | 12 | 7 | judge.json first | 74 (silk_over_copper 25, silk_overlap 49) |
| stickhub | primary | 900.0 | -15 (timeout) | 8 | 1 | lowest cost; stopped at 900 s | 3 (solder_mask_bridge 3) |
| stickhub | pours removed | 900.0 | -15 (timeout) | 8 | 1 | lowest cost; stopped at 900 s | 1 (solder_mask_bridge 1) |

## Refusals and crashes, verbatim

agent-ee `place run` (`reason` from `job-report.json`):

| Board | Cell | Status | Text | Seconds |
| --- | --- | --- | --- | --- |
| flat_hierarchy | place | refused | `board-level zones with pours or placement constraints are unsupported; routing-only rule areas must allow footprints and pads` | 1.899 |
| flat_hierarchy | place-nopours | refused | `only a rectangular board outline is supported by this job model` | 1.827 |
| flat_hierarchy | route | refused | `board-level zones with pours or placement constraints are unsupported; routing-only rule areas must allow footprints and pads` | 1.594 |
| flat_hierarchy | route-nopours | refused | `only a rectangular board outline is supported by this job model` | 1.72 |
| tigard | place | refused | `placement engine failed (exit 1): "J4" ("00000000-0000-0000-0000-00005eec90db") pad "4ae2ff61-5967-43a1-9c33-212d7b4e7b82": contour 3 prepared contour vertex total 16440 exceeds 16384 (contour count 73)` | 2.06 |
| tigard | route | refused | `placement engine failed (exit 1): "J4" ("00000000-0000-0000-0000-00005eec90db") pad "4ae2ff61-5967-43a1-9c33-212d7b4e7b82": contour 3 prepared contour vertex total 16440 exceeds 16384 (contour count 73)` | 1.913 |
| esp32_devkit_lipo | place | refused | `board-level zones with pours or placement constraints are unsupported; routing-only rule areas must allow footprints and pads` | 1.252 |
| esp32_devkit_lipo | place-nopours | refused | `only a rectangular board outline is supported by this job model` | 1.577 |
| esp32_devkit_lipo | route | refused | `board-level zones with pours or placement constraints are unsupported; routing-only rule areas must allow footprints and pads` | 1.511 |
| esp32_devkit_lipo | route-nopours | refused | `only a rectangular board outline is supported by this job model` | 1.436 |
| complex_hierarchy | place | refused | `board-level zones with pours or placement constraints are unsupported; routing-only rule areas must allow footprints and pads` | 1.33 |
| complex_hierarchy | place-nopours | refused | `only a rectangular board outline is supported by this job model` | 1.407 |
| complex_hierarchy | route | refused | `board-level zones with pours or placement constraints are unsupported; routing-only rule areas must allow footprints and pads` | 1.731 |
| complex_hierarchy | route-nopours | refused | `only a rectangular board outline is supported by this job model` | 1.62 |
| stickhub | place | refused | `board-level zones with pours or placement constraints are unsupported; routing-only rule areas must allow footprints and pads` | 4.09 |
| stickhub | place-nopours | refused | `board outline is not one proven contour` | 4.403 |
| stickhub | route | refused | `board-level zones with pours or placement constraints are unsupported; routing-only rule areas must allow footprints and pads` | 4.127 |
| stickhub | route-nopours | refused | `01343d3d-4853-4738-a3f9-e885006c7455: orientation 45.0 is not a quarter turn` | 4.169 |

Other failures:

- kicad-route run directly (secondary) on flat_hierarchy and
  complex_hierarchy, with and without pours, and inside agent-ee's job on the
  debug board sonde_u: `ValueError: Invalid native copper-text bounds`
  (`kicad_parser.py`, `_copper_text_obstacles`), router exit 1,
  `backend produced no candidate`.
- KRT `make_unplaced.py` on complex_hierarchy before deviation 1:
  `refuse: … carries routed copper. Strip it first, keeping the outline
  bit-identical`.
- KRT `place_simple.py` on ESP32-DevKit-LiPo, with and without pours: exit 4,
  all 12 seeds listed `illegal` (parts left without a legal pose). The
  lowest-cost seed was routed anyway; its placement alone has 20
  `shorting_items` errors.
- KRT `place_simple.py` on StickHub, with and without pours: stopped by the
  900 s budget after 8 of 12 seeds (one process).
- agent-ee on esp_prog, seeds 4-7: `deadline` at 900 s, reached during final
  checks after a routed board existed.
- Debug board sonde_u, pours removed (outside the set; the only other board
  agent-ee's placer accepted):
  - compiler-default search: `deadline` after 900.034 s (search 898.404 s); no placement, no route.
  - 12 seeds x 10,000 steps: `deadline` after 900.038 s (search 898.707 s); no placement, no route.
  - KRT's placement of the same board, routed by stock KRT: 0 unconnected, 34 errors, 12 vias, 659.12 mm.

## Tool defects observed

Not fixed here. Component, then what happened.

1. **`placement_job` (capture):** refuses any board with a copper pour. Four
   of six real boards carry one.
2. **`placement_job` (job model):** refuses any board whose outline is not a
   plain rectangle (`only a rectangular board outline is supported by this job
   model`; StickHub's unplaced variant: `board outline is not one proven
   contour`). Three of the four boards that got past the pour check.
3. **`placement_job` / engine:** refuses a board holding a part at 45 degrees
   (`orientation 45.0 is not a quarter turn`), even when every footprint is
   locked and nothing is to be placed.
4. **Placement engine (`crates/placement-engine`, prepare):** contour-vertex
   cap. tigard's J4 has a pad whose prepared contours total 16,440 vertices
   against a cap of 16,384; the job is refused, again even with every
   footprint locked.
5. **`placement_job`:** a routing-only job (all footprints fixed) still has to
   pass every placement-model check above. There is no supported way to route
   an existing placed board that the placement model does not accept.
6. **`placement_job` (budget):** with the design compiler's default search (4
   seeds, 100,000 steps) the debug board sonde_u (22 movable parts) spent
   898 s in search, finished 1 of 4 seeds and ended `deadline` with no
   placement and no route. The finished, feasible seed was kept only in the
   checkpoint. With 12 seeds of 10,000 steps it finished 4 of 12 seeds in
   898 s and ended the same way. On esp_prog (16 movable parts) the default
   search took 693 to 736 s of the 900 s budget, and one of three runs ended
   `deadline` during final checks.
7. **Placement engine (speed):** about 1.7 ms per search step on esp_prog
   (693 s for 400,000 steps); its 12-seed, 10,000-step search took 547 s. On
   sonde_u, 40,000 steps took 898 s (about 22 ms per step). KRT's placer ran
   its 12 seeds in 57 s on esp_prog and 154 s on sonde_u.
8. **`placement_job` (error attribution):** on sonde_u with every footprint
   locked, the 35 `copper_edge_clearance` errors already in the input were
   counted as `placement_introduced`, so no candidate could become the
   incumbent (`routed_candidate: best_diagnostic_exploratory`).
9. **kicad-route (parser):** `Invalid native copper-text bounds` crash on
   boards with copper text (sonde_u, flat_hierarchy, complex_hierarchy). Stock
   KRT reads and routes the same files.
10. **kicad-route (router), through agent-ee on esp_prog:** the routed board
    has a `shorting_items` error, 6 `hole_clearance` and 2 new `clearance`
    errors at the 0.025 mm grid the job selected. The job detects this and
    reports failure; it has no further repair.
11. **kicad-route (router), direct runs:** leaves nets open where stock KRT at
    defaults closes them (tigard 17 against 0, ESP32-DevKit-LiPo 21 against 4,
    StickHub 4 against 0) within the same budget.
12. **Placement intent:** `fixed_poses` cannot name a footprint with an empty
    reference (`expected nonempty text`).
13. **Host installation:** `agent-ee doctor` reports nothing about kicad-route
    unless `--router-checkout` is given. For `~/git/kicad-route` it then lists
    four problems (state manifest written for another path, router generation
    built from commit `f42b8796` and not the checkout, installation record
    differs, `kicad_cli` hash drift), and for the image's verified copy one
    (`state manifest drift: kicad.kicad_cli.sha256`). `place run` was
    therefore run only inside the CI image; a host run was not attempted.

Observed in stock KRT, for context: at defaults it routes below the board's
own minimum track, via and clearance values and rewrites the project file to
match (`rescued a gap: … track 0.127`); the README-default install pairs HEAD
Python with a prebuilt router from an older tag; see the placer notes for its
placement failures.

## Limits of this comparison

- **One matched board.** Every head-to-head number comes from esp_prog, a
  21-footprint, two-layer board from KRT's own repository. No independent
  board produced an agent-ee result. One board cannot rank two tools; the
  refusal count is the more robust finding.
- One run per cell. KRT is deterministic per seed; agent-ee was run on three
  seed sets on esp_prog only.
- The groups file is mechanical (one group per schematic sheet, often a
  single group). Neither placer got an engineer's floorplan, and agent-ee's
  intent carried no connector edges or proximity claims, while KRT derived
  its own intent from the board. KRT's placer is meant to be followed by a
  judging route over six seeds; here only its first-ranked seed was routed.
- The lock rule keys on reference prefixes. On ESP32-DevKit-LiPo the
  connectors and buttons have other prefixes and were left movable for both
  tools.
- KRT's `make_unplaced.py` is the unplacer for both tools; it also drops
  small zones tied to moved parts and moves copper text of moved parts.
- "The board's own rules" for a board without a project file are KiCad's
  defaults, which KRT's defaults do not honour. Both scores are shown; a
  score under one rule set is not comparable with a score under the other.
- agent-ee ran in a container and KRT on the host. The times include neither
  container start (about 2 s) nor admission.
- Stock KRT was run through `route.py` alone, not its `autoplan.py` chain or
  its agent-driven planning skills, which pour planes and fan out first.
- DRC error counts include silkscreen findings where a board's project
  raises them to errors (complex_hierarchy).
- Not run: a stretch board of about 200 parts; seed repeats on any board but
  esp_prog; the full test suite (by instruction).

---

## Rerun after AGENTEE-635/636 (2026-11-09/10)

Written after the tool changes of AGENTEE-635 (copper pours) and AGENTEE-636
(outlines, rotated parts, contour limit, routing-only jobs). Only the
agent-ee cells were run again, on the same prepared boards as the first run
(`bench.py reuse` copies them and checks the `route-in` and `place-in`
hashes), with pours kept, the same 900 s budget and the same scoring on the
host. The stock KRT rows are copied from the tables above; KRT was not run
again. Run window: 2026-11-09 23:43 to 2026-11-10 01:05 UTC on host proximal,
at most two jobs at a time. Raw results:
`~/.local/share/agent-ee/krt-matched-20261109-r2/` (not committed). The
preregistered text and the first run's results above are unchanged.

### Result in brief

- **`place run` now accepts all six boards in both arms** (12 of 12 cells;
  2 of 12 in the first run). No cell ended with one of the five refusals
  listed under "Refusals and crashes, verbatim".
- **Routing arm:** four boards were routed and scored. Two (flat_hierarchy,
  complex_hierarchy) were accepted, checked and handed to the router, which
  crashed on its copper-text parser (`Invalid native copper-text bounds`,
  defect 9 above; kicad-route, not changed here).
- **Placement arm:** esp_prog repeated its first-run result. On the other
  five boards the job was accepted and then spent the whole budget in the
  placement search (`deadline`, defects 6 and 7 above): no placement was
  selected and nothing was routed.
- No routed agent-ee board passed agent-ee's own final check
  (`verification_failed`, `INELIGIBLE`, router `router_accepted: false` in all
  four), and agent-ee left nets open on three of the four boards it routed
  where stock KRT left none or fewer. Acceptance is fixed; the comparison
  with stock KRT is still not won.

### Versions

| Item | Version |
| --- | --- |
| agent-ee | `909f3454` (tool changes), clean, for 7 cells. The placement cells of flat_hierarchy, tigard and esp32_devkit_lipo were recorded on `909f3454` with uncommitted edits, and those of complex_hierarchy and stickhub at `fee9265a`; in all five only `tests/` and `docs/` differed from `909f3454`. `src/`, `crates/` and the engine binary are the same in all 12. |
| agent-ee CI image | `sha256:3c7234a3bcc6…` (`agent-ee:exec-343ea66cd64cd338`), Python 3.12.3, KiCad 10.0.8 |
| Placement engine | `agent-ee-placement 0.1.0`, image binary sha256 `61982a440b91…`, built from this branch's crate |
| kicad-route | `995891ae8852f6524815d5f06ad3a062f40ed8e0`, clean, the image's `/opt/kicad-route` (as in the first run) |
| KiCad (host, scoring) | `kicad-cli` 10.0.8, pcbnew 10.0.8 |
| Stock KRT rows | first run, `e6bc31e2` |

### Routing arm (pours kept)

"Accepted" means capture, engine preparation and the placement check
completed and a candidate reached the router. DRC errors are native
`kicad-cli pcb drc --refill-zones` against the board's own rules, scored on
the host as before; the unrouted input's count is given beside it. Seconds
are the job's `elapsed_s`.

| Board | Tool | Accepted | Unconnected | DRC errors (input) | Vias | Track mm | Error types | Seconds | Tool's own verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| esp_prog | stock KRT (first run) |  | 0 | 70 (1) | 24 | 310.36 | see above | 6 | exit 0 |
| esp_prog | agent-ee `place run` | yes | 0 | 10 (1) | 31 | 293.82 | clearance 2, hole_clearance 6, shorting_items 2 | 43 | `verification_failed` |
| flat_hierarchy | stock KRT (first run) |  | 0 | 2 (2) | 39 | 1641.68 | clearance 2 | 23 | exit 0 |
| flat_hierarchy | agent-ee `place run` | yes | no routed board |  |  |  | router crashed: `ValueError: Invalid native copper-text bounds`; job reason `KRT exited 1` | 23 | `refused`, `failure_kind: environment`, stage `routing` |
| tigard | stock KRT (first run) |  | 0 | 594 (48) | 168 | 1785.78 | see above | 104 | exit 0 |
| tigard | agent-ee `place run` | yes | 9 | 48 (48) | 117 | 1766.67 | clearance 25, copper_edge_clearance 2, courtyards_overlap 2, drill_out_of_range 16, shorting_items 1, solder_mask_bridge 2 | 210 | `verification_failed` |
| esp32_devkit_lipo | stock KRT (first run) |  | 4 | 746 (43) | 175 | 1250.70 | see above | 143 | exit 0 |
| esp32_devkit_lipo | agent-ee `place run` | yes | 18 | 54 (43) | 100 | 781.11 | copper_edge_clearance 38, hole_clearance 10, items_not_allowed 1, starved_thermal 4, zones_intersect 1 | 461 | `verification_failed` |
| complex_hierarchy | stock KRT (first run) |  | 0 | 0 (0) | 14 | 1186.61 | - | 12 | exit 0 |
| complex_hierarchy | agent-ee `place run` | yes | no routed board |  |  |  | router crashed: `ValueError: Invalid native copper-text bounds`; job reason `KRT exited 1` | 20 | `refused`, `failure_kind: environment`, stage `routing` |
| stickhub | stock KRT (first run) |  | 0 | 43 (0) | 83 | 569.83 | see above | 32 | exit 0 |
| stickhub | agent-ee `place run` | yes | 2 | 29 (0) | 87 | 553.66 | solder_mask_bridge 29 | 252 | `verification_failed` |

Under the rules KRT wrote for itself, the stock KRT boards had 5, 2, 8, 48, 0
and 30 errors (first run). The capture records of the six routing jobs:
all `routing_only: true`, no placement model built; outline `rectangle` on
esp_prog and tigard and `polygon` on the other four; pour zones 0, 1, 0, 3,
1, 5. The job's own final check kept `integrity: preserved` on all four
routed boards, after it refilled and saved the pours. It attributed the
errors as: esp_prog 1 pre-existing and 9 routing-introduced; tigard 48
pre-existing; esp32_devkit_lipo 40 pre-existing and 14 routing-introduced;
stickhub 33 routing-introduced (the host score of the same file counts 29
`solder_mask_bridge`; the difference was not investigated).

### Placement arm (pours kept)

| Board | Cell | Accepted | Unconnected | DRC errors | Vias | Track mm | Seconds | Outcome |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| esp_prog | KRT placer → KRT route (first run) |  | 0 | 12 | 16 | 228.94 | 57 (placer) |  |
| esp_prog | agent-ee placer → KRT route | yes | 0 | 15 | 31 | 336.14 | 833 (job) | same as the first run |
| esp_prog | agent-ee placer → agent-ee route | yes | 1 | 15 | 30 | 293.23 | 833 | `verification_failed` |
| flat_hierarchy | KRT placer → KRT route (first run) |  | 0 | 2 | 36 | 1271.05 | 239 (placer) |  |
| flat_hierarchy | agent-ee `place run` | yes | no placement |  |  |  | 900 | `deadline`; search 898 s, no seed finished (54 movable parts) |
| tigard | KRT placer → KRT route (first run) |  | 1 | 861 | 192 | 2146.60 | 822 (placer) |  |
| tigard | agent-ee `place run` | yes | no placement |  |  |  | 900 | `deadline`; search 898 s, no seed finished (76 movable parts) |
| esp32_devkit_lipo | KRT placer → KRT route (first run) |  | 23 | 402 | 111 | 838.25 | 191 (placer) |  |
| esp32_devkit_lipo | agent-ee `place run` | yes | no placement |  |  |  | 900 | `deadline`; 1 of 4 seeds finished, not feasible (48 movable parts) |
| complex_hierarchy | KRT placer → KRT route (first run) |  | 0 | 74 | 17 | 948.40 | 166 (placer) |  |
| complex_hierarchy | agent-ee `place run` | yes | no placement |  |  |  | 900 | `deadline`; 1 of 4 seeds finished and feasible, kept only in the checkpoint (61 movable parts) |
| stickhub | KRT placer → KRT route (first run) |  | 21 | 387 | 133 | 685.73 | 900 (placer, stopped) |  |
| stickhub | agent-ee `place run` | yes | no placement |  |  |  | 900 | `deadline`; 2 of 4 seeds finished, not feasible (80 movable parts) |

### What changed in the tool

1. Copper pours are kept, refilled after a move and after routing, and judged
   by native DRC with `--refill-zones`; rule areas that forbid footprints or
   pads are fixed obstacles.
2. Any closed outline with arcs and cut-outs is accepted through KiCad's own
   outline builder; arcs become segments within the board's maximum error
   (5 µm on these boards).
3. A footprint may sit at any angle; the part of the angle that is not a
   quarter turn stays with the part.
4. The whole-problem contour-vertex limit is gone (tigard carries 16,440).
5. A job in which nothing can move builds no placement model.

Also changed because these boards needed it: fixed footprints may repeat a
reference; a fixed footprint without pads or body drawing is no body
(StickHub's mask-only logo); an error the unchanged input already reports
between unmoved items is counted as pre-existing (defect 8 above). The
details are in [`docs/placement-job.md`](../placement-job.md).

### Tool defects this rerun exposed or confirmed

Not fixed here.

1. **kicad-route (parser), defect 9 above:** now the only reason
   flat_hierarchy and complex_hierarchy have no agent-ee routing result.
2. **`placement_job` (budget) and engine speed, defects 6 and 7 above:** with
   the design compiler's default search no board of 48 to 80 movable parts
   produced a placement in 900 s. The search finished 0, 0, 1, 1 and 2 of 4
   seeds; the one feasible seed (complex_hierarchy) was not used because the
   deadline ended the job.
3. **`placement_job` (status):** a router crash is reported as `status:
   refused` (with `failure_kind: environment`). The board was not refused.
4. **kicad-route (router), defect 11 above:** through `place run` it left 9,
   18 and 2 nets open on tigard, ESP32-DevKit-LiPo and StickHub; stock KRT
   left 0, 4 and 0.
5. **kicad-route (router), defect 10 above:** new `hole_clearance` errors on
   esp_prog (6) and ESP32-DevKit-LiPo (10), a second `shorting_items` on
   esp_prog, and 29 `solder_mask_bridge` on StickHub.
6. **`placement_job` (final check):** the supplemental group views are not
   rendered for a board whose outline is not four straight lines
   (`group_views: missing` on ESP32-DevKit-LiPo and StickHub). They do not
   decide eligibility.
7. **`placement_job` (judge):** a routing-only job still materializes and
   checks one identical candidate per seed (four on these runs), about 5 to
   8 s of repeated native DRC.

### Limits of this rerun

- One run per cell. StickHub's routing result differed between a trial run
  during development and this run (2 open nets in both; 32 against 29
  errors), so the router is not repeatable to the error.
- Stock KRT was not rerun; its rows are the first run's, on the same inputs.
- The zone-fallback variants (pours removed) were not rerun.
- The placement changes for outlines, rotated movable parts and keep-out
  rule areas are covered by unit tests on synthetic boards. No benchmark
  cell exercised them to a finished placement, because every placement job
  on a board with those features ended at the deadline. The routing arm did
  exercise pours, polygon outlines and 45 degree parts end to end.
- The full test suite was not run (by instruction).

---

## Rerun after AGENTEE-637 (2026-11-10)

Written after the tool changes of AGENTEE-637 (placement search budget, engine
speed, default search size). Only the agent-ee placement cells were run again,
on the same prepared boards as the first run (`bench.py reuse`, hashes
checked), with pours kept, the same 900 s budget, the same routing of the
chosen placement by stock KRT and the same scoring on the host. The stock KRT
rows are copied from the first run; KRT was not run again. Run window:
2026-11-10 02:43 to 04:25 UTC on host proximal, at most two jobs at a time.
Raw results: `~/.local/share/agent-ee/krt-matched-20261109-r3/` (not
committed). Everything above this section is unchanged.

### Result in brief

- **Every board now gets a placement** (6 of 6; 1 of 6 before). No job spent
  its budget in the search: each search ended `completed` inside its share of
  the time (329 to 353 s of a 359 s share) and its candidates went on to the
  native checks and the router.
- **Only esp_prog's placement is one agent-ee itself accepts** (a checked
  incumbent). On the other five the job offers its best diagnostic candidate,
  which is not valid: on flat_hierarchy and complex_hierarchy every seed was
  feasible for the engine and native DRC then reported errors the engine does
  not model (`starved_thermal` on an unrouted poured board; silkscreen findings
  that complex_hierarchy's project raises to errors); on tigard the engine
  found feasible states and the candidate still was not eligible; on
  ESP32-DevKit-LiPo and StickHub no seed reached a feasible state.
- **Routed by the same stock KRT, agent-ee's placements are worse than KRT's
  placer on vias and track length on all six boards.** They leave fewer nets
  open on ESP32-DevKit-LiPo (10 against 23) and more on tigard (8 against 1)
  and StickHub (50 against 21).
- Two jobs (tigard, ESP32-DevKit-LiPo) ended `deadline` in agent-ee's own
  routing; the placement they had checked is named in the report and was
  routed by KRT here.
- The placement comparison with stock KRT is still not won. What changed is
  that it can now be made on every board.

### Versions

| Item | Version |
| --- | --- |
| agent-ee | `5c1ba455` (branch `agentee-637-placement-budget`), clean, all 12 cells |
| agent-ee CI image | `sha256:ff3f11dc5a21…` (`agent-ee:exec-8629520c85ba9f55`), Python 3.12.3, KiCad 10.0.8 |
| Placement engine | `agent-ee-placement 0.1.0`, image binary sha256 `9140c7d2faaf…`, built from this branch's crate |
| kicad-route | the image's `/opt/kicad-route`, as in the earlier runs (recorded per cell in `job_preflight`) |
| KiCad (host, scoring) | `kicad-cli` 10.0.8, pcbnew 10.0.8 |
| Stock KRT (router for both placements) | `e6bc31e2`, the first run's clone |
| Stock KRT placer rows | first run, `e6bc31e2` |

### Placement arm (pours kept)

Search setting: the design compiler's default, which since AGENTEE-637 is 4
clustered seeds, shortlist 4 and no step count (the engine sizes the seeds to
the time). "Ended" is the job's own status. "Seconds" is the job's `elapsed_s`
for agent-ee and the placer's wall clock for KRT. Unconnected, DRC errors (the
board's own rules), vias and track length are of the placement routed by stock
KRT `route.py` at defaults.

| Board | Placer | Placement produced | Ended | Seconds | Unconnected | DRC errors | Vias | Track mm |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| esp_prog | KRT (first run) | yes | exit 0 | 57 | 0 | 12 | 16 | 228.94 |
| esp_prog | agent-ee | yes, checked incumbent | `verification_failed` (own route) | 507 | 0 | 31 | 29 | 317.72 |
| flat_hierarchy | KRT (first run) | yes | exit 0 | 239 | 0 | 2 | 36 | 1271.05 |
| flat_hierarchy | agent-ee | yes, diagnostic (not valid) | `verification_failed` | 535 | 0 | 6 | 119 | 2391.78 |
| tigard | KRT (first run) | yes | exit 0 | 822 | 1 | 861 | 192 | 2146.60 |
| tigard | agent-ee | yes, diagnostic (not valid) | `deadline` (in own routing) | 900 | 8 | 1143 | 245 | 2655.89 |
| esp32_devkit_lipo | KRT (first run) | yes, all seeds illegal | exit 4 | 191 | 23 | 402 | 111 | 838.25 |
| esp32_devkit_lipo | agent-ee | yes, diagnostic (not valid) | `deadline` (in own routing) | 900 | 10 | 977 | 200 | 1630.17 |
| complex_hierarchy | KRT (first run) | yes | exit 0 | 166 | 0 | 74 | 17 | 948.40 |
| complex_hierarchy | agent-ee | yes, diagnostic (not valid) | `verification_failed` | 446 | 0 | 164 | 45 | 1331.01 |
| stickhub | KRT (first run) | yes, 8 of 12 seeds | stopped at 900 s | 900 | 21 | 387 | 133 | 685.73 |
| stickhub | agent-ee | yes, diagnostic (not valid) | `placement_infeasible` | 673 | 50 | 406 | 190 | 890.65 |

Under the rules KRT wrote for itself, the KRT-routed agent-ee placements have
3, 6, 8, 27, 122 and 99 errors (same board order); the KRT placer rows had 4,
2, 8, 48, 74 and 55. Errors of the placement alone, before routing: 1, 4, 48,
9, 118 and 2.

agent-ee's own route of its own placement, same job:

| Board | Unconnected | DRC errors | Vias | Track mm |
| --- | ---: | ---: | ---: | ---: |
| esp_prog | 2 | 2 | 24 | 300.13 |
| flat_hierarchy | 2 | 7 | 96 | 2164.63 |
| tigard | no routed board (deadline) |  |  |  |
| esp32_devkit_lipo | no routed board (deadline) |  |  |  |
| complex_hierarchy | 4 | 121 | 36 | 1128.04 |
| stickhub | 56 | 36 | 138 | 617.40 |

How each search used its 359 s share (4 seeds planned):

| Board | Movable parts | Seeds run | Steps per seed | Seeds feasible for the engine | Search s |
| --- | ---: | ---: | --- | ---: | ---: |
| esp_prog | 13 | 4 | 1.74 M to 2.68 M | 4 | 331 |
| flat_hierarchy | 54 | 4 | 294 k to 426 k | 4 | 347 |
| tigard | 76 | 2 (2 dropped by the plan) | 158 k, 320 k | 2 | 353 |
| esp32_devkit_lipo | 48 | 4 | 315 k to 431 k | 0 (fewest hard violations 4) | 331 |
| complex_hierarchy | 61 | 4 | 499 k to 600 k | 4 | 333 |
| stickhub | 80 | 4 | 207 k to 273 k | 0 (fewest hard violations 78) | 329 |

### Secondary: the earlier default, 100,000 steps per seed

The same cells with `place --capped` (4 seeds, 100,000 steps, shortlist 4, the
default before AGENTEE-637), to show what the default change did. One run per
cell.

| Board | Job s | Search s | Ended | Unconnected | DRC errors | Vias | Track mm |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| esp_prog | 145 | 20 | `verification_failed` | 0 | 15 | 31 | 336.14 |
| flat_hierarchy | 382 | 138 | `verification_failed` | 0 | 4 | 84 | 2178.25 |
| tigard | 900 | 356 | `deadline` (in own routing) | 8 | 1200 | 277 | 2850.73 |
| esp32_devkit_lipo | 900 | 109 | `deadline` (in own routing) | 24 | 1127 | 230 | 1585.94 |
| complex_hierarchy | 199 | 68 | `verification_failed` | 0 | 112 | 49 | 1407.09 |
| stickhub | 422 | 135 | `placement_infeasible` | 51 | 449 | 176 | 813.12 |

esp_prog here repeats the first run's placement exactly (31 vias, 336.14 mm):
a seed that runs its full step count is reproducible. Against these rows the
sized default is better on esp_prog, tigard, ESP32-DevKit-LiPo and
complex_hierarchy, worse on flat_hierarchy (119 vias against 84) and mixed on
StickHub (one net fewer open; more vias and track). With one run per cell this
does not establish that the longer search routes better.

### Engine step rate

Before: the engine of `dde31c53`. After: `5c1ba455`. "Fixed test" is one
clustered seed of 2,000 steps on the captured problem of each board, run on
the host back to back; the two engines returned the same result. "In the job"
is the rate over whole seeds in the benchmark jobs, two jobs at a time: for
the earlier runs the bound that follows from the seeds that finished in the
898 s search, for this run the engine's own `steps_per_s`.

| Board | Fixed test before, ms/step | Fixed test after, ms/step | In the job before, ms/step | In the job after, ms/step |
| --- | ---: | ---: | --- | ---: |
| esp_prog | 1.416 | 0.091 | 1.7 | 0.04 |
| flat_hierarchy | 12.549 | 0.772 | above 9.0 (no seed finished) | 0.19 to 0.25 |
| tigard | 19.826 | 1.543 | above 9.0 (no seed finished) | 0.55 to 0.58 |
| esp32_devkit_lipo | 8.766 | 0.393 | 4.5 to 9.0 (1 seed finished) | 0.21 to 0.24 |
| complex_hierarchy | 6.059 | 0.362 | 4.5 to 9.0 (1 seed finished) | 0.13 to 0.17 |
| stickhub | 6.726 | 0.564 | 3.0 to 4.5 (2 seeds finished) | 0.33 to 0.37 |

The fixed test ran while other jobs loaded the host; an earlier quiet run of
the same test gave 0.042 ms/step on esp_prog and 0.193 on ESP32-DevKit-LiPo
after the change. Where the time went before, measured by timing the sections
of one trial on flat_hierarchy (1,500 steps, 15.8 s): 7.0 s in the group term,
which placed every member of a group, exact pad contours included, to read its
body centre; 5.3 s placing each neighbour of a moved part again for every
trial; 2.7 s placing the moved parts; 0.5 s on pairs inside a group that moved
as one. All four grow with the pads per part and the group size, which is why
the cost per step rose from board to board. After a seed, the member
refinement graded every seat with the full evaluator: 47 s on flat_hierarchy
for a 6 s anneal of 20,000 steps.

### What changed in the tool

1. **A job always offers the best placement it checked.** The search takes a
   share of the time left (`search.time_share`, default 0.4) and the engine
   sizes itself to it: the time is shared between the seeds, each seed cools
   fully inside its share, and seeds are dropped when the measured step rate
   cannot serve them. A search that used up its time ends `completed` and its
   candidates are checked and routed. `placement` in the job report names the
   placement in every ending, `deadline` included, with `deadline_limited`.
2. **The engine is faster.** Exact pad contours are transformed only when a
   box test cannot decide; committed placements are kept; a group moved as one
   skips its unchanged inner pairs; the group term reads body boxes only;
   refinement uses the incremental evaluator and has a time limit. Results
   are identical step for step.
3. **The default search has no step count.** The design compiler wrote 4
   seeds of 100,000 steps. Measured on esp_prog and sonde_u, the cost was
   still falling at 250,000 steps per movable part (esp_prog, median of the
   feasible seeds: 1,036 M at 2,000 steps per part, 999 M at 25,000, 916 M at
   250,000), so a seed now runs for its share of the time unless the design
   gives a count.

### Why agent-ee's esp_prog placements route worse than KRT's (measured, not fixed)

- Graded by agent-ee's own engine, KRT's twelve esp_prog placements cost
  1,004 M to 1,244 M; ten are feasible. agent-ee's 100,000-step seeds cost
  965 M to 1,022 M. The objective does not tell KRT's best placement (16 vias,
  229 mm) from agent-ee's (31 to 39 vias, 336 to 343 mm).
- The group-link term is 58 to 61 % of that cost (588 M to 621 M of 965 M to
  1,022 M); net bounding-box length is 25 %. The benchmark's one group per
  schematic sheet makes every part a member of one group, so each part is
  linked to the anchor by any pad pair and every net forms links between all
  its members.
- With the link and group weights set to zero and 1,000,000 steps, three of
  four seeds routed with 14 to 16 vias and 234 to 246 mm (KRT's kept seed: 16
  and 229); the fourth with 37 vias and 3 nets open. With the weights as they
  are and 1,000,000 steps: 20 to 35 vias, 245 to 359 mm. At 100,000 steps the
  zero-weight search gave 18 to 37 vias.
- A spanning-tree net length in place of the bounding box did not help
  (19 to 40 vias over 11 placements).
- These experiments ran the engine on the host on the captured esp_prog
  problem and routed with stock KRT; they are not benchmark cells and are from
  one board. No weight was changed in the tool.

### Tool defects this rerun exposed or confirmed

Not fixed here.

1. **`placement_job` (judge):** `starved_thermal` on a placed, unrouted board
   with pours counts as an error the placement introduced, so no candidate can
   become the incumbent (flat_hierarchy: 2 or 3 per candidate, every seed
   feasible for the engine). The thermal spokes cannot exist before routing.
2. **Engine (model):** silkscreen findings a project raises to errors are not
   modelled (complex_hierarchy: 118 to 158 per candidate, every seed feasible
   for the engine). KRT's placement of the same board has 74.
3. **Engine (search):** no feasible state on ESP32-DevKit-LiPo (fewest hard
   violations 4: board-edge copper clearance and occupancy outside the
   outline) or StickHub (78) in 207 k to 431 k steps per seed. KRT's placer
   also found no legal placement on ESP32-DevKit-LiPo.
4. **Engine (objective):** see the esp_prog section above; on every board the
   KRT-routed result has more vias and track than KRT's placement.
5. **`placement_job` (budget):** the router took all the time left on tigard
   (543 s) and ESP32-DevKit-LiPo (562 s) and the job ended `deadline` without
   a routed board. The routing phase has no reserve for the final checks.
6. **`placement_job` (time on small boards):** with no step count, esp_prog's
   search used its whole share (331 s) although 20 s gave a comparable result
   in the capped run. The search has no stopping rule other than time.
7. **tigard:** the engine reported two feasible seeds and the job still
   offered only a diagnostic candidate. The cause was not investigated.
8. Defects 9 to 11 of the first run (kicad-route) are unchanged and were not
   touched.

### Limits of this rerun

- One run per cell. A seed limited by time follows the clock and is not
  reproducible; five of the six default cells are of that kind in every seed.
- The jobs ran two at a time, which lowers the step rate and so the steps a
  time-sized seed gets.
- Stock KRT's placer was not rerun; its rows are the first run's.
- The routing arm and the zone-fallback variants were not rerun.
- Tests: the modules that import the changed code were run in the CI image
  (59 modules, 827 tests, on the commit before the last two source changes;
  101 tests of 8 modules on the final commit). The full suite was not run (by
  instruction).

## Rerun with the fixed router, and a placer weight test (2026-11-10)

Two questions: does routing through `agent-ee place run` improve with
kicad-route `f074b4a` (AGENTEE-638), and does agent-ee's placement route better
when the pull of each schematic-sheet group toward its anchor is reduced or
off. Same prepared boards (hashes checked), same 900 s budget, KiCad 10.0.8,
CI image `sha256:85e8151f3e82`, agent-ee `cd5564dd`. Raw results:
`~/.local/share/agent-ee/krt-matched-20261109-r4/` and
`krt-matched-20261110-weights-{off,quarter}/`.

### Routing arm through `place run`

| Board | Router `995891ae` (r2) | Router `f074b4a` (r4) | Job status (r4) |
|---|---|---|---|
| esp_prog | 0 open, 10 errors | 0 open, 4 errors (3 shorts added) | `verification_failed` |
| flat_hierarchy | refused (router crash) | 0 open, 2 errors, both pre-existing | `verification_failed` |
| tigard | 9 open, 48 errors | 9 open, 48 errors, all pre-existing | `verification_failed` |
| ESP32-DevKit-LiPo | 18 open, 54 errors | 18 open, 43 errors | `verification_failed` |
| complex_hierarchy | refused (router crash) | 0 open, 0 errors | `integrity_failed` |
| StickHub | 2 open, 29 errors | 2 open, 30 errors | `verification_failed` |

The crashes and the hole-clearance errors are gone. No open net closed on
tigard, ESP32-DevKit-LiPo or StickHub. No board is accepted by the job.

### Placement arm with changed weights

The weight table is `WEIGHTS` in `src/agent_ee/placement_problem.py`. "Off"
sets `group` and `link` to 0. "One quarter" multiplies every other weight by 4
and leaves `group` and `link` at 1. Each placement was stripped of copper and
routed by stock KRT, as before. Cells are open nets / vias / track length in
mm. KRT's placer and the default weights are the earlier runs' rows.

| Board | KRT's placer | Default weights (r3) | Group off | Group one quarter |
|---|---|---|---|---|
| esp_prog | 0 / 16 / 229 | 0 / 29 / 318 | 0 / 24 / 298 | 0 / 33 / 279 |
| flat_hierarchy | 0 / 36 / 1271 | 0 / 119 / 2392 | 0 / 37 / 1419 | 0 / 104 / 2077 |
| tigard | 1 / 192 / 2147 | 8 / 245 / 2656 | 2 / 229 / 2189 | 4 / 233 / 2615 |
| ESP32-DevKit-LiPo | 23 / 111 / 838 | 10 / 200 / 1630 | 17 / 212 / 1574 | 21 / 249 / 1571 |
| complex_hierarchy | 0 / 17 / 948 | 0 / 45 / 1331 | 0 / 20 / 989 | 0 / 31 / 1149 |
| StickHub | 21 / 133 / 686 | 50 / 190 / 891 | 54 / 154 / 786 | 58 / 152 / 752 |

- With the group pull off, the placement routes with less copper than the
  default on five boards and about level on ESP32-DevKit-LiPo. It is close to
  KRT's placer on flat_hierarchy, complex_hierarchy and tigard, and still
  behind it on every board except in open nets on ESP32-DevKit-LiPo (where
  KRT's placement itself has 49 errors before routing).
- One quarter is between the two; the effect is not all-or-nothing.
- StickHub is not explained by this term: 50 to 58 nets stay open against
  KRT's 21 in all three, and the job reports `placement_infeasible` each time.
- agent-ee's own routing of these placements still ends `deadline` on
  ESP32-DevKit-LiPo and leaves 32 to 42 nets open on tigard.

No weight was changed in the tool. The groups here are mechanical (one per
sheet, anchor = the part with most pads), so this shows the term hurts with
such groups, not that grouping is wrong when an engineer declares it.

### Tool defects this rerun exposed

- complex_hierarchy routes with 0 open nets and 0 DRC errors and the job
  rejects it: `identity_problems` says the router changed protected native
  content. flat_hierarchy gets the same message. Whether the router changed
  such content or the comparison is wrong was not determined.
- The group-view step fails on every board whose outline is not four straight
  lines (`native fit-board crop is qualified only for four-line rectangular
  Edge.Cuts`, `unsupported nonrectangular native crop`): four of six boards.
  AGENTEE-636 removed this limit from the job's capture but not from this step.
- esp_prog: the router adds 3 `shorting_items`.

### Limits

One run per cell, so differences of a few vias are within unknown noise. The
runs started after other sessions restarted on the host; the load was below 4
at the start and was not recorded afterwards. KRT's rows were not rerun.

## The accepted measure, and where both tools stand (2026-11-11)

On 2026-11-10 the owner accepted three recommendations: move the router onto
current upstream KRT, judge a result by whether it is fully routed and clean
against the board's own rules, and stop pulling parts toward the anchor of a
group that was derived from a schematic sheet. This section applies that
measure to every run so far, adds KRT's own label step and a rule-obeying
configuration of KRT to the comparison, and reports agent-ee's cells before
and after the tool changes of 2026-11-10 and 11.

**Every KRT figure here is for KRT's scripts run once**: its placer with the
benchmark's mechanical groups, its router at its defaults or held, and its
label script. KRT documents a much longer procedure for an AI agent to run
around those scripts. That procedure was run on one board only, the one that
separates the tools, and it did not change the verdict there. What it is and
what it delivered is under "KRT's own procedure" below.

Same prepared boards as before (hashes checked), 900 s budget, KiCad 10.0.8,
kicad-route `f074b4a`, stock KRT `e6bc31e2`. The router change of AGENTEE-650
(kicad-route `bcaaa31`) came after every run in the tables; what was measured
with it is under "A net class with its own via size". KRT's default cells and
its placements are those of 2026-11-09 and were not rerun; its label step and
its held routes were run on those placements on 2026-11-11. agent-ee "before" is
`776030a9` (routing cells) and `13181913` or `094d6594` (placement cells);
agent-ee "now" is `1c352464` in both arms. Raw results under
`~/.local/share/agent-ee/`: `krt-matched-20261109/` (KRT),
`krt-matched-20261111/` (KRT held, routing arm), `krt-matched-20261111-fab2/`
(stated fab rules), `-r5/` and `-r6/` (agent-ee before), `-r11/` (agent-ee
now, placement arm), `-r12/` (agent-ee now, routing arm), `-r7/`, `-r9/` and
`-r10/` (intermediate states of the same changes), `-r13/` (StickHub's
placement cell with AGENTEE-647).

### The verdict

A cell **passes** when the board has no open connection and no DRC error
beyond the errors of the unrouted input, scored by
`kicad-cli pcb drc --severity-all --refill-zones` beside the board's own rule
files. A finding is matched by its type and the descriptions of its items, so
an error the source design already has does not count against a tool.
`bench.py summary` prints the verdict for every cell (AGENTEE-643).

What "the board's own rules" are:

- esp_prog, tigard and ESP32-DevKit-LiPo come without a project file, so
  their own rules are KiCad's defaults. tigard cannot be routed under them:
  the pads of its resistor networks are 0.15 mm apart and the default
  clearance is 0.2 mm. esp_prog and tigard are therefore also run with stated
  fab rules (`fab-rules.json`: the `standard` tier of upstream KRT's own
  table, plus a 0.2 mm copper-to-hole clearance in revision 2). Revision 1
  left KiCad's 0.25 mm default for that one rule, which the tier's via cannot
  meet; its tigard results are void.
- "KRT held" is stock KRT with `--escalation board --no-fix-drc-settings`: it
  may deliver a net below its net class's width or via size, but not below
  the board's stated minimums, and it does not rewrite the project's rules.
  KRT at its defaults goes below the board's minimums. These are the flags
  KRT's own procedure prescribes when the project's rules are the user's own.
- On esp_prog every tool's new errors are between new copper of one net and a
  copper polygon of U2 that has no net and lies over that net's pad. No tool
  passes it; AGENTEE-641 measured a floor of one error.

Two things the verdict does not see are reported beside it:

- **New warnings.** A rule the board sets to `warning` does not fail a cell.
- **Features below their net class's size.** KiCad's DRC compares a track or
  a via with the board's minimums, not with its net class. A router can pass
  the verdict by delivering a power net narrower than its class asks.

### KRT's own label step

agent-ee's job moves labels after it has placed the parts (AGENTEE-644).
KRT's placer does not, and the first runs compared agent-ee with KRT's
untidied board. KRT's placement instructions end with a separate step: tidy
the reference labels with `py_placer/beautify_labels.py`, and keep the result
only when KiCad counts fewer silkscreen findings. The harness now runs that
on the board KRT placed and routed, by KRT's own keep rule (cells
`...-labels`). Every KRT placement cell below includes it.

| Board | Silkscreen findings before | After KRT's label step | Of those, errors under the board's rules |
|---|---|---|---|
| esp_prog | 59 | 56 | 0 |
| flat_hierarchy | 70 | 37 | 0 |
| tigard | 46 | 46, tidied board not kept | 0 |
| ESP32-DevKit-LiPo | 405 | 361 | 0 |
| complex_hierarchy | 74 | 10 | 10 |
| StickHub | 7 | 1 | 0 |

complex_hierarchy is the one board whose rules make silkscreen findings
errors. Ten remain after KRT's step: nine labels on the drawn silk of a
neighbouring part, which the script says it does not check, and the polarity
mark of D303 on a pad of D304, which no label step can move. agent-ee's board
has none. The step changes no pass or fail on any board.

### KRT's own procedure

KRT's scripts as run here are its placer (`place_simple.py`, default seeds,
the benchmark's mechanical groups), its router (`route.py`, at its defaults
or held) and its label script. That is not how KRT documents its use. It
ships two procedures written for an AI agent, `plan-pcb-placement` (1,663
lines) and `plan-pcb-routing` (4,184 lines). In them the agent sorts the
parts into functional groups by its own judgement, declares connector edges
and datasheet layouts, places 12 seeds, trial-routes the six the placer's
objective rates best and keeps the one that routes best, routes that board
by a plan written for it, tidies the labels, and has a second agent verify
the result. Its own cap for a run is ten hours. The benchmark keeps the
first seed the objective names and routes it once.

Three things were established about the board that separates the tools,
complex_hierarchy: what the procedure says, what its choice of seed could
change, and what one full run of it delivers.

KRT's procedure has no step aimed at the errors that fail its board there.
Its finish step says so: "no DONE grader reads silk", the label tidy is the
one step that looks at silkscreen, and that script "does not see drawn silk,
a part's own outline included". The benchmark runs that step by the
procedure's own keep rule. Nine of the ten errors left are labels on the
drawn silk of a neighbouring part and one is a polarity mark on a pad. KRT's
own grade of that board (`grade.py --mode full --whose-rules users-own`)
reads `done: true`, `drc: 0`: by KRT's gate the run would end there.

The seed choice would not change it either. The procedure trial-routes the
six seeds the placer names and keeps the one that routes best. Each of those
six was finished here the way the procedure finishes a board (routed by KRT
held, labels tidied by its keep rule) and scored by the verdict
(`bench.py krt-judged`; cells `place-judged-...` in `krt-matched-20261109/`):

| Seed, in the placer's order | Open | Errors after routing | Errors after the label step | Vias | Track, mm |
|---|---|---|---|---|---|
| 7, the one the benchmark kept | 0 | 74 | 10 | 17 | 948 |
| 8 | 0 | 57 | 2 | 20 | 973 |
| 10 | 0 | 71 | 2 | 22 | 1,083 |
| 11 | 0 | 76 | 5 | 24 | 1,124 |
| 5 | 0 | 61 | 4 | 18 | 1,023 |
| 2 | 0 | 80 | 13 | 24 | 1,088 |

Every error is a silkscreen finding. None of the six passes. By the
procedure's own order (fewest vias, then least copper) it would keep seed 7,
the benchmark's.

That table keeps the benchmark's mechanical groups and one `route.py` run.
The procedure itself was then run once on this board, in full mode
(AGENTEE-652), by an agent that is not this report's author: Claude Opus as
a worker in a private copy of the KRT checkout, from the benchmark's
unplaced board with the project's schematic beside it, as the procedure
expects. Nobody was there to answer its questions, the project's rules were
declared the user's own, and the worker was not told the benchmark's verdict
or any earlier result. It took about 20 minutes. The agent chose six
functional groups from the schematic's sheets and blocks, placed 12 seeds,
trial-routed six under the two routing recipes the procedure prescribes for
a two-layer through-hole board and confirmed the close ones at full level,
kept seed 4, routed it both ways and kept the route with no via, tidied the
labels (88 silkscreen findings before, 8 after), filled the zones, and had a
second agent verify the board, which passed it.

| complex_hierarchy | Open | New errors | Vias | Track, mm | KRT's own grade |
|---|---|---|---|---|---|
| KRT's scripts once (held routing, label step) | 0 | 10 | 17 | 948 | done |
| KRT's full procedure | 0 | 8 | 0 | 935 | done |
| agent-ee (run r11) | **0** | **0** | 32 | 1,142 | |

The eight errors are silkscreen findings (silk_overlap 7, silk_over_copper
1). So on the one board tried the procedure does not change the verdict:
KRT's board fails it, and KRT calls that board done. It does route the board
better than either tool's automatic run, with no via at all. The scored cell
is `place-procedure-krt-full-procedure` in `krt-matched-20261109/`; the run's
folder and the worker's deviations from the procedure (no REPORT.md, one
refused replication step, estimated net currents) are listed in AGENTEE-652.

Still not measured: the procedure on any other board, and a second run or a
second executor on this one. "KRT held" is the routing the procedure
prescribes when the project's rules are the user's own
(`--escalation board --no-fix-drc-settings`).

### Result in brief

- Placing and routing on its own, agent-ee now delivers two of the six
  boards fully routed with no new error (flat_hierarchy and
  complex_hierarchy), and three of eight with the two fab-rules variants
  (tigard). KRT's scripts, run once with its label step, deliver one of six
  at their defaults (flat_hierarchy; one of eight) and one of six held to the
  board's rules (two of eight, with tigard). Before the changes of this
  section agent-ee delivered one of six (complex_hierarchy; one of eight).
- So the claim the numbers support is: better than KRT's scripts run once,
  by one board. KRT's documented use is an agent-driven procedure around
  those scripts. It was run on that one board and did not change the verdict
  there; on the other boards it was not run, so the numbers do not support
  "better than KRT" in general.
- The board that separates the tools on the six is complex_hierarchy: KRT's
  board keeps 10 silkscreen errors after KRT's own label step, agent-ee's has
  none. None of the six placements KRT's judge would choose from passes (2
  to 13 errors each), and one full run of KRT's procedure by an independent
  agent ends with 8 silkscreen errors on a board KRT's own grade calls done:
  its graders do not read silkscreen. That run routes the board with no via,
  which neither tool's automatic run does.
- Routing the designer's placement, the counts are level: two of six for
  KRT's router at its defaults, held, and for agent-ee. With the fab-rules
  variants it is two of eight for KRT's router at its defaults and three of
  eight for KRT's router held and for agent-ee.
- No tool passes esp_prog, tigard under KiCad's defaults, ESP32-DevKit-LiPo
  or StickHub in either arm. In the placement arm agent-ee and KRT's scripts
  held each leave 23 to 31 connections open on the last three; KRT's scripts
  at their defaults leave 1 to 23 open and add 385 to 846 errors.
- Where both pass, KRT's board has less copper (flat_hierarchy 1,271 mm
  against 1,499 mm). On flat_hierarchy KRT narrows 7.5 mm of a power net
  below its class width to get there; agent-ee's board has no track below its
  class.
- In these runs agent-ee's own job accepts fewer boards than the verdict
  passes: it rejects flat_hierarchy in both arms because vias of the class
  `Wide` are 0.6 mm where the class asks 1.0 mm. That was a defect of the
  router, which stock KRT shares, and it is fixed since (AGENTEE-650): on
  both flat_hierarchy boards agent-ee's router now delivers every via at its
  class size with no open connection. Its job accepts the board in the
  routing arm (72 s against 486 s) and in one of two runs of the placement
  arm (540 s against 840 s). The other run failed on a starved thermal
  relief, which the router before the change produces on the same placement
  (AGENTEE-654). KRT's boards keep 12 of 13 such vias at 0.6 mm.

### Routing arm (designer's placement, pours kept)

Cells are open connections / new errors. A bold cell passes.

| Board | Rules | KRT's router, default | KRT's router, held | agent-ee before | agent-ee now | As designed |
|---|---|---|---|---|---|---|
| esp_prog | KiCad defaults | 0 / 69 | 0 / 4 | 0 / 3 | 0 / 3 | source has no tracks |
| flat_hierarchy | own | **0 / 0** | **0 / 0** | **0 / 0** | **0 / 0** | source has no tracks |
| tigard | KiCad defaults | 0 / 546 | 11 / 0 | 9 / 0 | 9 / 0 | source has no tracks |
| ESP32-DevKit-LiPo | KiCad defaults | 4 / 706 | 10 / 20 | 18 / 3 | 18 / 2 | 0 / 34 |
| complex_hierarchy | own | **0 / 0** | **0 / 0** | **0 / 0** | **0 / 0** | **0 / 0** |
| StickHub | own | 0 / 43 | 6 / 22 | 2 / 35 | 2 / 36 | **0 / 0** |
| esp_prog, fab rules r2 | stated | 0 / 10 | 0 / 4 | 0 / 2 | 0 / 2 | source has no tracks |
| tigard, fab rules r2 | stated | 0 / 42 | **0 / 0** | **0 / 0** | **0 / 0** | source has no tracks |

New errors by type, where a cell fails: KRT default on tigard, track_width
199, clearance 179, drill_out_of_range 55, via_diameter 55, annular_width 52,
hole_clearance 6 (199 is KiCad's limit for one finding type, so the true
count may be higher); on tigard with fab rules, annular_width 12,
drill_out_of_range 12, via_diameter 12, hole_clearance 6: twelve vias below
the stated minimum. StickHub's new errors are solder_mask_bridge for every
tool (KiCad's count of these varies by a few between runs of the same
board). ESP32-DevKit-LiPo as designed has 34 errors its unrouted input does
not have, 31 of them copper too close to the board edge under KiCad's
defaults.

agent-ee's own job accepts two of these boards (`completed`):
complex_hierarchy in 72 s, and now tigard with fab rules in 76 s. Before, its
job chose the 0.025 mm grid for tigard, which left two vias of one net 0.1 mm
apart; KiCad reports that as a warning, the job rejected its board for it and
stopped. In these runs it rejects flat_hierarchy, for a reason the verdict
does not see (next table) and AGENTEE-650 has since removed, and takes 486 s
there, because the rejected board gets a second trial.

This column was measured twice, in run r9 at an earlier state of the same
change and in r12 at the final commit. The router delivered the same copper
on every board both times. KiCad's count differs on two of them: StickHub
has 37 and 36 solder_mask_bridge findings, and on esp_prog with fab rules the
same two findings are two clearance errors in one run and a clearance error
and a short in the other.

What the verdict does not see, on the passing cells:

| Cell | New warnings | Below net class size |
|---|---|---|
| flat_hierarchy, KRT default and held | 1 (silk_overlap) | 12 vias of class `Wide` at 0.6 mm (class: 1.0 mm) |
| flat_hierarchy, agent-ee | 0 | 15 vias of class `Wide` at 0.6 mm |
| complex_hierarchy, all three | 0 | none |
| tigard with fab rules, KRT held | 0 | none |
| tigard with fab rules, agent-ee before | 1 (hole_to_hole) | none |
| tigard with fab rules, agent-ee now | 0 | none |

Both engines read a via size per net class. In these runs neither used it to
search a net of more than two pads, and the vias such a net was given at the
Default size stayed that size wherever the larger one did not fit. agent-ee's
job rejects a board for such vias; KRT does not check them. The cause, the
fix in agent-ee's router and what was measured with it are under "A net class
with its own via size" below. The first version of this section said that no
engine takes a via size per net class. That was wrong.

### Placement and routing (pours kept)

Cells are open connections / new errors. KRT's cells include its label step.
"agent-ee" is agent-ee placing and routing on its own.

| Board | Rules | KRT's scripts, default routing | KRT's scripts, held routing | agent-ee before | agent-ee now | agent-ee's placement now, KRT held routing |
|---|---|---|---|---|---|---|
| esp_prog | KiCad defaults | 0 / 11 | 0 / 3 | 1 / 1 | 0 / 1 | 0 / 1 |
| flat_hierarchy | own | **0 / 0** | **0 / 0** | 3 / 0 | **0 / 0** | **0 / 0** |
| tigard | KiCad defaults | 1 / 846 | 23 / 0 | no result (`deadline`) | 28 / 0 | 19 / 0 |
| ESP32-DevKit-LiPo | KiCad defaults | 23 / 398 | 24 / 75 | no result (`deadline`) | 31 / 8 | 24 / 19 |
| complex_hierarchy | own | 0 / 10 | 0 / 10 | **0 / 0** | **0 / 0** | **0 / 0** |
| StickHub | own | 21 / 385 | 25 / 49 | no result (`deadline`) | 24 / 59 | 26 / 43 |
| esp_prog, fab rules r2 | stated | 0 / 8 | 0 / 8 | 0 / 1 | 0 / 1 | 0 / 1 |
| tigard, fab rules r2 | stated | 0 / 34 | **0 / 0** | 11 / 0 | **0 / 0** | **0 / 0** |

- **Before**, agent-ee had no board on tigard, ESP32-DevKit-LiPo and StickHub:
  the job ended `deadline`. **Now** every cell has a board.
- On ESP32-DevKit-LiPo and StickHub the job's status is
  `placement_infeasible`: its search found no placement without a hard
  conflict in its own model (the reasons are under the defects below). It
  then routes its best candidate as an exploratory result and says so. Those
  boards are the cells shown.
- tigard under KiCad's defaults: three placements were routed (35, 28 and 48
  open) and the time left allowed no second trial. Neither tool can finish
  this board under these rules (see the verdict).
- esp_prog: all four placements route with no open connection and one new
  error each, on both grids. It is the finding described under the verdict,
  new copper of one net against the net-less polygon of U2, reported as a
  clearance error or as a short, and one is the floor AGENTEE-641 measured.
  KRT's placement routed by KRT held has three of them.
- New errors by type where a cell fails. KRT default: tigard clearance 499,
  track_width 199 (both at KiCad's limit), drill_out_of_range 42,
  via_diameter 42, annular_width 36, hole_clearance 28; ESP32-DevKit-LiPo
  track_width 174, clearance 115, and 109 others; StickHub track_width 199,
  solder_mask_bridge 55, and 131 others. KRT held: ESP32-DevKit-LiPo
  track_width 28, shorting_items 20, solder_mask_bridge 20, and 7 others;
  StickHub solder_mask_bridge 49. agent-ee now: ESP32-DevKit-LiPo
  starved_thermal 4, track_width 4; StickHub solder_mask_bridge 59.
- The job's own status is `completed` on complex_hierarchy and on tigard
  with fab rules. On flat_hierarchy it is `verification_failed`, for the via
  size of the class `Wide` alone (fixed since, AGENTEE-650).
- Time to place and route the boards agent-ee passes: flat_hierarchy 840 s
  (KRT 258 s), complex_hierarchy 415 s (KRT 175 s), tigard with fab rules
  398 s (KRT 771 s). 330 s of each agent-ee job is a search whose length the
  budget sets.

Where both tools pass, KRT's board has less copper: flat_hierarchy 36 vias
and 1,271 mm against agent-ee's 49 and 1,499 mm; tigard with fab rules 160
vias and 1,906 mm against 168 and 1,974 mm.

What the verdict does not see, on the passing cells:

| Cell | New warnings | Below net class size |
|---|---|---|
| flat_hierarchy, KRT (default and held routing) | 35 (silk_overlap 24, silk_over_copper 11) | 7.5 mm of class `Wide` track at 0.2 mm (class: 0.5 mm), 2 mm more in tapers, 12 vias at 0.6 mm |
| flat_hierarchy, agent-ee now | 33 (silk_overlap 19, silk_over_copper 14) | 25 vias of class `Wide` at 0.6 mm; no track below its class |
| complex_hierarchy, agent-ee before and now | 0 | none |
| tigard with fab rules, KRT held routing | 23 (silk_over_copper 18, silk_overlap 5) | none |
| tigard with fab rules, agent-ee now | 25 (silk_over_copper 23, silk_overlap 2) | none |

The new warnings are silkscreen that did not move while the parts did. On
flat_hierarchy all 33 of agent-ee's, and 31 of KRT's 35, are between the
board's own annotation texts ("VCC ON", "PWR ON", "I2C PROM") and a part that
now sits on them. On tigard 16 of agent-ee's 25 are logo artwork and a part
that now sits on it, 7 are the label "MODE" of a switch that stays in place,
and 2 are a moved part's outline over a pad of that switch; KRT's 23 are of
the same kinds. Neither tool keeps moved parts clear of such content where
the board's rules only warn (AGENTEE-651). On complex_hierarchy, where the
rules make it an error, agent-ee's capture reserves that content and its
board has none.

### Why agent-ee's placements were worse, and what changed

On tigard with the fab's rules stated, stock KRT held to the rules routes
KRT's own placement with no open connection, and left 10 open on agent-ee's
placement. The same router, so the difference was the placement.

The placer's cost had a term that added one link for every pair of parts in a
group that share a net. The benchmark's groups are mechanical, one per
schematic sheet, and tigard is one sheet: one group of 85 parts, 1,982 such
links, 1,225 of them on GND and 406 on +3V3, because the benchmark declares
no rail. In the placer's own model:

| Placement of tigard | Wire (half perimeter), mm | Link term, mm | Routed by KRT held |
|---|---|---|---|
| Designer's | 1,427 | 28,014 | (routing arm) 0 open |
| KRT's | 1,487 | 29,358 | 0 open, passes |
| agent-ee before, the two valid candidates | 1,859 and 1,976 | 19,776 and 19,641 | 10 open |
| agent-ee now, run r10, four valid candidates | 1,396 to 1,561 | none | 1 open |
| agent-ee now, run r11, the two valid candidates | 1,407 and 1,612 | none | 0 open, passes |

The search was minimising the link term, which is mostly "every GND part
close to every other GND part", and paid for it with a quarter to a third
more wire. AGENTEE-637 had removed the pull toward the anchor for a sheet
group and kept these links; the weight test behind that change had switched
both off. A sheet group now adds no cost at all (AGENTEE-648). Groups an
engineer declares are unchanged; for those the pairwise form still grows with
the square of a net's members, which is not addressed here.

The same change on the other boards, lowest wire among the candidates in the
placer's model, before and now: flat_hierarchy 1,810 and 1,256 mm; tigard
under KiCad's defaults 1,907 and 1,442; esp_prog 219 and 198;
ESP32-DevKit-LiPo 1,173 and 1,100 and StickHub 668 and 405 (both among
candidates with hard conflicts).

complex_hierarchy is the one board here whose sheets are functional blocks
(three groups, 94 links), and its routed length went from 851 mm before to
883 mm in r10 and 1,142 mm in r11. So the old and the new engine were run
side by side on its captured problem: four searches each, two of each at a
time, so that the host's load fell on both alike. Wire of the candidate each
search ranked first: 1,070, 1,017, 1,022 and 946 mm with the links; 983,
1,026, 1,012 and 957 mm without. Over all 16 seeds of each the mean is
1,093 mm with the links and 1,038 mm without. The change costs this board
nothing. One search differs from the next by about a tenth, and the 874 mm
candidate behind the 851 mm of the run before was one seed of four.

Three smaller changes to the job's routing schedule came out of the same
cells (AGENTEE-645, 649):

- A placement whose routing fails for something the routing made gets one
  second trial on the router's other grid, with the time the first trials
  left. No one grid is better on every board (table further down).
- Every candidate's first trial uses the router's default grid, which is
  about five times faster than 0.025 mm. Before, a board with pads under 1 mm
  pitch started on 0.025 mm: on tigard three valid candidates each got a
  third of the time on that grid and none finished, while the default grid
  routes the first in under a minute.
- A job in which no trial completes reports the best board among its failed
  trials, not the last one.

On flat_hierarchy the new placements route with no open connection and no
track below its class width on agent-ee's own router (before: 3 open). KRT's
own pass on that board narrows 7.5 mm of a power net to 0.2 mm.

### What KRT does to complete agent-ee's old flat_hierarchy placement

Kept for the record, because it explains the earlier "agent-ee placement,
KRT routing" passes. Stock upstream KRT (0.23.9) on agent-ee's flat_hierarchy
placement of run r5, one setting changed at a time:

| Run | Open | New errors | Class `Wide` track below 0.5 mm |
|---|---|---|---|
| kicad-route's flag and environment set (no relaxation) | 2 | 1 | none |
| the same with `--escalation board` | 2 | 1 | none |
| the same, wide nets may neck down into pads | 0 | 1 | 31 mm at 0.2 mm |
| the same, KRT's terminal escalation allowed | 0 | 1 | 98 mm at 0.4 mm |
| the same flags, KRT's default environment | 0 | 0 | 95 mm at 0.4 mm |
| KRT held (`--escalation board --no-fix-drc-settings`) | 0 | 0 | 12 mm at 0.2 mm |
| KRT held, wide nets may not neck down | 0 | 0 | 95 mm at 0.4 mm |

Every run that completes that placement delivers three or four nets of the
class `Wide` narrower than the class asks, and says so (29 to 31 features).
All of it is above the board's own minimum of 0.2 mm, so KiCad's DRC accepts
it. kicad-route forbids it and left connections open. With the placement
fixed, the question did not arise on this board again.

### The same engines on the fab-rules boards, both grids

Direct router runs on the designer's placement, 900 s budget, open
connections / new errors, and the verdict of kicad-route's own grading, which
also rejects a new warning. "Candidate" is upstream KRT `772003a6` with the
local changes kept so far (AGENTEE-642, not landed); "upstream, strict" is
upstream unchanged with kicad-route's flags.

| Board | Engine | Default grid | 0.025 mm grid |
|---|---|---|---|
| tigard, fab rules r2 | kicad-route `f074b4a` | 0 / 0, graded PASS (23 s) | 0 / 0, graded FAIL: 1 hole_to_hole warning (130 s) |
| tigard, fab rules r2 | candidate | 0 / 0, PASS (27 s) | 0 / 0, PASS (124 s) |
| tigard, fab rules r2 | upstream, strict | 0 / 0, PASS (24 s) | 0 / 0, PASS (147 s) |
| esp_prog, fab rules r2 | kicad-route `f074b4a` | 0 / 4 | 0 / 2 |
| esp_prog, fab rules r2 | candidate | 0 / 4 | 0 / 2 |
| esp_prog, fab rules r2 | upstream, strict | 0 / 4 | 0 / 2 |

With the rules stated correctly every rule-obeying engine routes tigard
completely. The 163 hole-clearance errors of revision 1 came from the rule
set, not from a router. On the boards under KiCad's defaults the 0.025 mm
grid leaves fewer connections open (tigard 9 against 17, ESP32-DevKit-LiPo 18
against 21, StickHub 2 against 4); on agent-ee's old flat_hierarchy placement
it leaves more (4 against 3).

### A net class with its own via size (AGENTEE-650)

flat_hierarchy puts four nets in the class `Wide`, whose via is 1.0 mm with a
0.7 mm drill. The Default class's is 0.6 mm with 0.4 mm. In the runs above
both tools deliver most vias of those nets at the Default size:

| flat_hierarchy | Vias of class `Wide` at 1.0 mm | At 0.6 mm |
|---|---|---|
| Designer's placement, KRT's router held | 1 | 12 |
| Designer's placement, agent-ee (r12) | 3 | 15 |
| KRT's placement, KRT's router held | 1 | 12 |
| agent-ee's placement, agent-ee (r11) | 1 | 25 |

KiCad's DRC compares a via with the board's minimum, 0.5 mm here, not with
its class, so the verdict passes all four boards. kicad-route's own grading
does not, and for that reason alone agent-ee's job rejected its own board in
both arms (`verification_failed`). Its further trials could not change a via
size, and they took 409 of the routing cell's 486 s and 407 of the placement
cell's 840 s.

The cause is in the engine, and current upstream KRT has it as the fork does.
The engine reads the via size of each net's class. It searches a two-pad net
with that via. A net with more than two pads it searches with the run's via,
the Default class's, and afterwards enlarges each via only where the class
via happens to fit. The others it delivers small and reports as below the
requested size. The pad escape of its rescue step draws the run's via too.

kicad-route `bcaaa31` searches every net with its own class via, and gives a
net that has one no pad escape. Such a net gets its class via, or a
connection stays open and is reported. Nothing changes on a board whose
classes share one via size.

Measured so far with direct runs of the router on the two flat_hierarchy
boards agent-ee routed above, scored by the verdict:

| flat_hierarchy, agent-ee's router | Open / new errors | `Wide` vias at 1.0 mm | At 0.6 mm | All vias | Track, mm | kicad-route's grading | Router time |
|---|---|---|---|---|---|---|---|
| Designer's placement, before (first trial of r12) | **0 / 0** | 3 | 15 | 37 | 1,672 | FAIL | 53 s |
| Designer's placement, after | **0 / 0** | 21 | 0 | 42 | 1,674 | PASS | 50 s |
| agent-ee's placement of r11, before (first trial) | **0 / 0** | 1 | 25 | 49 | 1,499 | FAIL | 65 s |
| The same placement, after | **0 / 0** | 22 | 0 | 51 | 1,502 | PASS | 73 s |

Every via of the class now has the class size, for five and two more vias
and 2 and 3 mm more track, and no connection is left open. The grading that
rejected both boards passes them, and it was the job's only reason to reject
them. KRT's boards keep 12 of their 13 `Wide` vias at 0.6 mm; KRT does not
check them.

The two rows "after" are runs with the flags and environment agent-ee's job
passes, in the test image of the runs above, with the router sources of
`bcaaa31` in place of the image's own.

agent-ee's own job was then run on these cells, with the inputs and settings
of r12 and r11 and nothing else changed, and on tigard with fab rules as a
board with one via size:

| Cell | Job status | Open / new errors | `Wide` vias at 1.0 mm | At 0.6 mm | All vias | Track, mm | Time |
|---|---|---|---|---|---|---|---|
| flat_hierarchy, routing arm, before (r12) | `verification_failed` | **0 / 0** | 3 | 15 | 37 | 1,672 | 486 s |
| flat_hierarchy, routing arm, after | `completed` | **0 / 0** | 21 | 0 | 42 | 1,674 | 72 s |
| flat_hierarchy, placement arm, before (r11) | `verification_failed` | **0 / 0** | 1 | 25 | 49 | 1,499 | 840 s |
| flat_hierarchy, placement arm, after | `completed` | **0 / 0** | 16 | 0 | 40 | 1,681 | 540 s |
| tigard with fab rules, routing arm, before (r12) | `completed` | **0 / 0** | | | 143 | 1,802 | 76 s |
| tigard with fab rules, routing arm, after | `completed` | **0 / 0** | | | 143 | 1,802 | 65 s |

The job now accepts flat_hierarchy in the routing arm, where its board is the
one the direct run delivered, and every via has its class size in both arms.
On tigard with fab rules the routed copper is the same as in r12, item by
item.

The placement cell depends on the run, as it did before the change. The
search is limited by time and finds other placements each time. In the run of
the table the job routed three of them. The first two came back with one new
error each, a ground pad whose thermal relief the routing had starved, and
the job rejected them. The third is clean and is the board reported. A second
run of the cell ended `verification_failed` after 768 s: its first placement
came back with the same kind of error, the retry on the finer grid left four
connections open, and no time was left for another placement. The board it
reports has no open connection and one new error, so it fails the verdict.

The change is not the cause of that error. The four placements routed in the
two runs were routed again with the router before the change:

| Placement | Before: open / new errors | `Wide` vias at 0.6 mm | After: open / new errors | `Wide` vias at 0.6 mm |
|---|---|---|---|---|
| Run 1, first | 0 / 1 | 21 of 22 | 0 / 1 | 0 of 23 |
| Run 1, second | 0 / 1 | 27 of 28 | 0 / 1 | 0 of 23 |
| Run 1, third | **0 / 0** | 14 of 17 | **0 / 0** | 0 of 16 |
| Run 2, first | 0 / 1 | 15 of 15 | 0 / 1 | 0 of 18 |

Both routers starve a thermal relief on the same three placements, and the
same error appeared once before, in r10. The job has a repair for it, a
second route with the spokes of the starved pad reserved, and that repair did
not run. kicad-route's report of starved pads does not recognise a
through-hole pad, and the job applies the repair only to pours it generated
itself (AGENTEE-654, open).

These job runs used the test image of the runs above with a clean checkout
of `bcaaa31` mounted in place of the image's router; the job records that
commit as its router. An image built with `bcaaa31` could not be made when
this was written: the build rechecks KiCad's package index on Launchpad,
which did not answer on 2026-11-11. Results are in
`~/.local/share/agent-ee/standin650/`.

kicad-route's own test fixture, a different board, showed the same defect: a
test had written it down as a limit of the backend. One via of a three-pad
net of the class Power was delivered at 0.6 mm with a 0.3 mm drill where the
class asks 0.8 mm and 0.4 mm. With the change both vias of that net have the
class size and the run passes every check. In kicad-route's whole suite, run
on the change and on the commit before it, that was the only test whose
outcome differed.

### What changed in the tool

- AGENTEE-640 (`f4e1a294`): the job no longer rejects a board for errors the
  input already had, does not count KiCad's teardrop zones as changed
  content, renders the group views for any closed outline, and states each
  rejection in words.
- AGENTEE-644 (`a5bb2151`): after placing the parts the job moves the labels
  that native DRC names in a silkscreen finding to a free place beside their
  part, and counts fixed silkscreen content as occupied. This is what takes
  complex_hierarchy from 78 errors to 0.
- AGENTEE-637 (`776030a9`): a group derived from a schematic sheet applies no
  pull toward its anchor. Parts on opposite sides of the board meet only
  where they physically can.
- AGENTEE-646 (`acaf5662`): an error between pads or drawings of one
  footprint is not booked as caused by the placement. KiCad stops listing a
  finding type at a limit (499 clearance findings, 199 of several others),
  and the unplaced input, a pile of footprints, has more; the input report
  left out 22 such errors on tigard, every placement there was called
  invalid, and the job only ever routed a diagnostic candidate.
- AGENTEE-645 (`5f3a9cb1`, `22896240`): the second routing trial, and whether
  the placement or the routing made a rejection (`final.made_by`).
- AGENTEE-648 (`21a27bf3`): a sheet group links none of its members.
- AGENTEE-649 (`ce38bd2d`, `1c352464`): the cheap grid first for every
  candidate; the best failed board is the one reported.
- AGENTEE-647 (`87417c9a`): on a board whose own rules ignore courtyard
  overlap, the body of a footprint is its package outline with the pads, not
  its courtyard. It landed after the runs above and changes StickHub alone
  among these boards; its cell is run r13, under the defects below.
- AGENTEE-650 (kicad-route `bcaaa31`): the router searches every net with
  its own class via, and a net that has one gets no pad escape at the
  Default size. It landed after the runs above; what it changes is under "A
  net class with its own via size".
- AGENTEE-643 (`0c7df73e`, `8fd0db46`, `13181913`, `094d6594`, `60788324`,
  and `krt-judged` with this report): in the harness, the verdict, the stated
  fab rules, the "KRT held" cells of both arms, KRT's label step, and the
  seeds KRT's judge would choose from.

### Tool defects this round exposed and did not fix

- When the router needs all the remaining time the job ends `deadline`, and
  with no earlier trial it has no board to report (flat_hierarchy in run
  r10). A first trial keeps no time back for the router's stop grace and
  grading: doing so would cut runs that now finish late.
- Moved parts land on silkscreen that stays in place where the board's rules
  only warn (AGENTEE-651; the warnings in the table above).
- StickHub does not route from either tool's placement. Its own rules set
  courtyard overlap to `ignore`. The board is 16.5 x 40 mm and its courtyards
  add up to 772 mm2 on 1,320 mm2 of two sides; the designer overlaps 143
  pairs of them and turns 39 of 94 parts by 45 degrees. In the runs above the
  placer took the courtyard as a hard body whatever the board says, so every
  candidate had hard conflicts and the job reported `placement_infeasible`.
  AGENTEE-647 takes the package outline as the body on such a board. With it
  (run r13) one seed of four finds a placement without a hard conflict, the
  other three keep one each (a part body over the mounting hole H1), native
  DRC finds no error the placement made, and the job ends
  `verification_failed` for its routing instead. The board still does not
  route: 27 open and 34 new errors on agent-ee's router (24 and 59 from the
  exploratory candidate before), 26 open with KRT held. The designer's
  placement routes with 2 open on agent-ee's router and 6 with KRT held, so
  on this board the placement of both tools is the larger gap.
- The engine models a part at 45 degrees by the upright rectangle around it.
  The designer's StickHub placement, which passes DRC, has 143 body conflicts
  in that model, 92 of them with a turned part, and 62 pad-opening conflicts,
  all with a turned part.
- ESP32-DevKit-LiPo: the two 19-pin header rows are as long as the board is
  tall. In the designer's own placement their courtyards overhang the
  outline by 0.13 mm and their pads are inside KiCad's default edge
  clearance (38 errors in the unrouted input). The engine requires a part it
  may move to lie inside the outline, the source does not lock the headers,
  and the benchmark's mechanical intent declares no edge part, so every
  placement is `placement_infeasible`. This is a limit of the benchmark's
  intent more than a defect: the job names the two headers.
- tigard with fab rules, 0.025 mm grid: the router places two vias of net
  VREF 0.1 mm apart, and on tigard under KiCad's defaults, default grid, a
  via in a pad of J1. Its own grading reports both. The causes in the engine
  were not looked for, because the engine is being replaced.
- The upstream engine with the local changes kept so far fails 12 of
  kicad-route's tests against 2 on main (AGENTEE-642). Not landed.

### Limits

One run per cell, except where more are named. The placement search is
limited by time, so a busier host gives it fewer steps and a different
placement. With the new placer, flat_hierarchy's placement cell ran three
times: it passes in r7 and r11, and in r10 the first routing trial did not
finish and the job had no board. complex_hierarchy passes in every run, with
851 mm of track before, 883 mm in r10 and 1,142 mm in r11 (the designer's
placement routes at 1,187 mm). Other sessions were running on the host
throughout: the load on its 12 threads was 3 to 13 at the start of most
cells, 16 to 19 during StickHub's placement cell in r5, up to 21 for some
minutes during r10 and 23 at the start of StickHub's cell in r11. Six boards,
eight with the fab-rules variants; a difference of one board is one board.
Stock KRT's default cells and its placements are from 2026-11-09. KRT is its
scripts run once, not the agent-driven procedure it documents (see "KRT's own
procedure"). That procedure was run once, on one board, by one executor; on
the other boards no figure here says what it would deliver. What is measured
after AGENTEE-650 is two direct runs of the router and four runs of the job,
two of them of the placement cell. KiCad's defaults are the rule set for
three boards.
The benchmark's groups and locks are mechanical: no engineer declared a rail,
an edge part or a part that overhangs the outline.

Every change of this section was found by studying these boards, and no board
outside the set has been run since. Each is a general rule (what a sheet
group may cost, which grid goes first, what counts as the placement's error,
what the body of a part is) and none names a board, but the set cannot show
how far they carry.
