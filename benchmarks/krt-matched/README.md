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

A rerun of one side after a tool change uses the very same prepared boards as
the run it is compared with: `KRT_BENCH_RUNS=NEW python3 $B reuse NAME OLD`
copies `norm`, `route-in`, `place-in` and `prep.json` from the earlier run
directory `OLD` and checks every board against the hashes in `prep.json`;
then run `baseline`, `route --tools agent-ee` and `place --tools agent-ee`.

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
  design compiler's default search (4 clustered seeds, shortlist 4, and since
  AGENTEE-637 no step count: the engine sizes the seeds to the budget; `place
  --capped` runs the earlier default of 100,000 steps per seed), `router.checkout` and the budget. No `router.*` option.
- **Placement arm routing**: both chosen placements are stripped of copper
  and routed by the same stock KRT `route.py`. agent-ee's placement is its
  routed board when the job produced one; otherwise the placement the job
  chose (`incumbent.json`, else `best-diagnostic.json`), recorded as
  `placement_kind`.
- **Search record**: every agent-ee cell records how the placement search used
  its time (`job_search`, `engine_budget`, one `engine_seeds` row per seed with
  its steps and measured step rate) and the placement the job offers
  (`job_placement`), which a job ending `deadline` also names.
- **Versions**: every agent-ee cell records the agent-ee commit (and whether
  the tree was dirty), the CI image ID, and the job's own preflight record
  (engine binary hash, router commit, pcbnew and kicad-cli versions).
- **Zone fallback**: `--no-pours` removes every pour zone and reruns both
  tools; reported separately.
- **Score**: `kicad-cli pcb drc --format json --severity-all --refill-zones`,
  plus via count and track length read with pcbnew, plus wall-clock time.
  KRT outputs get a second score beside the project file KRT wrote
  (`score_tool_rules`), because KRT lowers rule minimums to what it routed.
- **Verdict** (since 2026-11-10): a cell passes when its board has 0 open
  connections and no DRC error beyond those the unrouted routing input
  already has, against the board's own rule files. Vias, track length and
  time only rank cells that pass. A finding is matched to the input's by its
  type and the items it names, without positions, so a finding the design
  carries (two pads of one footprint closer than the rule) is not charged to
  a tool, and one a tool removes earns nothing. `summary` prints the verdict
  per cell and, per arm and tool, how many boards of the chosen set pass.
  `reference as-designed` rows score the source board where it is routed.
- **Rules origin**: `summary` says for each board whether its rules come from
  the source's project file or are KiCad's defaults because the source has
  none (esp_prog and tigard in the first set). On such a board a tool that
  chooses its own widths is not lowering anyone's rules; read those rows with
  that in mind.
- **Refusal**: when `place run` refuses a routing-arm board, `kicad-route run`
  is run directly with the adapter's base flags as a labelled secondary cell.

## Limits

One run per cell. Groups and the lock rule are mechanical, not an engineer's
floorplan. KRT is run at defaults, not through its agent-driven planning
skills. See the report for the limits of a specific run.
