# Agent notes for agent-ee

## What this is

agent-ee turns engineering intent into a manufacturable KiCad board: parts,
schematic, placement, routing, checks and a fabrication package. The product is
the tool. Example and benchmark boards are tests of the tool.

The goal is boards that are better than what stock
[KiCadRoutingTools](https://github.com/drandyhaas/KiCadRoutingTools) (KRT)
produces, measured on real boards. Current work is tracked in Plane under
AGENTEE-627.

## What counts as progress

A change is progress when it improves a measured board outcome on boards the
tool has not been tuned for, or removes a tool defect that a board run exposed:

- unconnected items and native DRC errors on the saved board;
- vias, track length and placement quality (grouping measured on the final
  component coordinates);
- wall time;
- size and kind of board the tool can take from requirements to a fabrication
  package.

Report results in those terms. Commits, merges, passing fixtures, review
verdicts and retained evidence are not outcomes.

## Rules

- Put the effort into electronics capability. Do not add or extend receipts,
  seals, custody or retention records, observers, supervisors, review
  committees, adjudication, grants, leases or holds. The owner directed on
  2026-11-09 that the guidance steering agents toward this machinery be
  removed ([decision](docs/decisions.md#direction-2026-11-09-capability-over-machinery)).
  If existing machinery blocks an ordinary change, remove the block and say so
  in the commit; do not build around it.
- Three habits are worth keeping, and no more: record the tool versions and
  commits a run used; treat a tool failure or missing output as a failure,
  never a pass; judge a board by native KiCad ERC and DRC on the saved files.
  The "non-negotiable evidence model" text in older documents is
  agent-written, not an owner directive.
- Agents forging results is outside the threat model
  ([owner ruling relayed 2026-10-22](docs/decisions.md#decisions-relayed-2026-10-22-agent-519-quiz-answers-and-agent-509-relays)).
  Do not design against it.
- Never tune a board, example, rule or limit to make one board pass. A failed
  board run is evidence of a tool defect: find the component and fix it
  generally.
- Use each dependency at its current version, fix that version for a run and
  record it.
- Money, orders, uploads of project files and owner-sourced constraints need
  the owner's direct answer. Do not label agent-written text as owner-stated.
- The telemetry pod is on hold (AGENTEE-518). Do not work on it.
- This repository is public. Do not commit third-party design files, libraries
  or datasheets without a recorded licence.

## Working here

```sh
bin/agent-ee doctor                                  # toolchain on this host
PYTHONPATH=src:tests /usr/bin/python3 -m unittest test_<module>   # one module
python3 scripts/check_docs.py                        # links and examples
python3 scripts/check_origins.py                     # run after git add
```

Run the tests for what you changed. The full suite takes about two hours and
runs nightly on the owner's host ([nightly suite](docs/nightly-full-suite.md));
do not run it for an ordinary change. Use `git-wt create <slug>` for a
worktree, land on `main`, and leave nothing unmerged.

One implementer per change is enough. Ask for a second opinion when you are
unsure about the engineering, not as a ritual.

## Where things are

- [README](README.md): what the tool does today and what it does not.
- [docs/README.md](docs/README.md): the manual.
- [docs/decisions.md](docs/decisions.md): owner decisions. The newest entry
  wins.
- [docs/history/](docs/history/README.md): retired process documents. They are
  records, not instructions.
