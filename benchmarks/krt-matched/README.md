# Matched benchmark: agent-ee against stock KRT

A small, repeatable comparison of agent-ee's placement and routing with stock
upstream KiCadRoutingTools (KRT) on real boards. The first run and its
preregistered board set are in
[`docs/benchmarks/krt-matched-2026-11-09.md`](../../docs/benchmarks/krt-matched-2026-11-09.md).

Files:

- `bench.py`: preparation, both arms, scoring and the summary table.
- `direct_route.py`: the labelled secondary router run used when
  `agent-ee place run` refuses a board (runs in the CI image).
- `inventory.py`: counts footprints, layers and zones for board selection.
- `boards.json`: the board set and the repositories it comes from.

No board is committed. Outputs go to
`~/.local/share/agent-ee/krt-matched-20261109/` (`KRT_BENCH_RUNS` overrides).

## Setup

```sh
mkdir -p ~/.cache/agent-ee-krt-bench/sources && cd ~/.cache/agent-ee-krt-bench
git clone https://github.com/drandyhaas/KiCadRoutingTools.git
cd KiCadRoutingTools
python3 -m venv .venv-bench
.venv-bench/bin/pip install -r requirements.txt
.venv-bench/bin/python build_router.py        # README default: prebuilt router
cd ../sources
git clone --depth 1 --filter=blob:none --sparse https://gitlab.com/kicad/code/kicad.git kicad-demos
git -C kicad-demos sparse-checkout set demos
git clone --depth 1 https://github.com/OLIMEX/ESP32-DevKit-LiPo.git
```

Stock KRT runs on the host from that clone. agent-ee runs through
`ae-ci-exec`, in the CI image built from this checkout, because that is where
its qualified router checkout (`/opt/kicad-route`) exists; on the host,
`agent-ee doctor --router-checkout` reports problems for every checkout
available. The job is staged under the gitignored `build/krt-matched/` (the
image mounts the checkout) and copied to the output directory afterwards.

## Run

```sh
B=benchmarks/krt-matched/bench.py
S=~/.cache/agent-ee-krt-bench
python3 $B prep  NAME $S/<source from boards.json>   # normalize, strip, lock, unplace
python3 $B baseline NAME                             # score the unrouted input
python3 $B route NAME                                # routing arm, both tools
python3 $B place NAME                                # placement arm, both tools
python3 $B summary                                   # table + summary.json
# zone fallback, only for a board agent-ee refused because of its pours:
python3 $B prep NAME SOURCE --no-pours && python3 $B route NAME --no-pours && python3 $B place NAME --no-pours
```

Optional extras: `krt-spread NAME` routes every seed KRT's placer wrote;
`place NAME --tools agent-ee --seed-offset N` repeats agent-ee's search on
other seeds; `place NAME --tools agent-ee --search-b` uses the search setting
of `docs/benchmarks/placement-job-2026-10-24.md` (12 seeds, 10,000 steps,
shortlist 6).

`--tools krt` or `--tools agent-ee` reruns one side. `KRT_BENCH_BUDGET_S`
(default 900) is the wall-clock budget of each routing or placement run.
Each cell writes `boards/NAME/results/<arm>-<tool>.json`; logs and boards are
under `boards/NAME/runs/`, the scored copies and DRC reports under
`boards/NAME/score/`.

## Generic rules

Nothing is keyed on a board. Every rule below applies to every board.

- **Normalize**: load and save the source once with the installed pcbnew. The
  saved `.kicad_pro` (and `.kicad_dru`) are the board's own rules and are
  restored beside every output before scoring, because routers may rewrite
  the project file.
- **Routing input**: tracks, vias and teardrop zones removed; every footprint
  locked; pours, rule areas and rules kept.
- **Placement input**: locks reset to source locks + footprints without a
  netted pad + reference prefixes `J P CN CON USB H MH SW`; everything else
  piled off the board by KRT's `make_unplaced.py --keep-locked`.
- **Groups** (same file for both placers): one group per schematic sheet,
  anchor = member with the most pads; only `name`, `anchor`, `refs`.
- **KRT**: `route.py IN OUT` with no option; `check_floorplan.py
  --emit-intent` then `place_simple.py` with its default seeds (`--jobs 1`),
  keeping the first seed its `judge.json` names.
- **agent-ee**: `place run` with paths and hashes, the groups file, an intent
  holding only the schema line and a fixed pose per locked, named footprint, the
  design compiler's default search (4 clustered seeds, 100,000 steps,
  shortlist 4), `router.checkout` and the budget. No `router.*` option.
- **Placement arm routing**: both chosen placements are stripped of copper
  and routed by the same stock KRT `route.py`.
- **Zone fallback**: `--no-pours` removes every pour zone and reruns both
  tools; reported separately.
- **Score**: `kicad-cli pcb drc --format json --severity-all --refill-zones`,
  plus via count and track length read with pcbnew, plus wall-clock time.
  KRT outputs get a second score beside the project file KRT wrote
  (`score_tool_rules`), because KRT lowers rule minimums to what it routed.
- **Refusal**: when `place run` refuses a routing-arm board, `kicad-route run`
  is run directly with the adapter's base flags as a labelled secondary cell.

## Limits

One run per cell. Groups and the lock rule are mechanical, not an engineer's
floorplan. KRT is run at defaults, not through its agent-driven planning
skills. See the report for the limits of a specific run.
