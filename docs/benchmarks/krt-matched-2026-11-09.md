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
