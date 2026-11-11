# Agent notes for agent-ee

## What this is

agent-ee turns engineering intent into a manufacturable KiCad board: parts,
schematic, placement, routing, checks and a fabrication package. The product is
the tool. Example and benchmark boards are tests of the tool.

The goal is boards that are better than what stock
[KiCadRoutingTools](https://github.com/drandyhaas/KiCadRoutingTools) (KRT)
produces, measured on real boards. The measurement is the
[matched benchmark](docs/benchmarks/krt-matched-2026-11-09.md). Its newest
"Result in brief" section says what the numbers support. Claim that and no
more. Current work is tracked in Plane under AGENTEE-627.

## What counts as progress

A change is progress when it improves a measured board outcome on boards the
tool has not been tuned for, or removes a tool defect that a board run exposed:

- open connections and native DRC errors on the saved board;
- vias, track length and placement quality (grouping measured on the final
  component coordinates);
- wall time;
- size and kind of board the tool can take from requirements to a fabrication
  package.

Report results in those terms. Commits, merges, passing fixtures, review
verdicts and retained evidence are not outcomes.

## What "done" means

These are three different claims. Say which one you are making.

- **A software change is done** when the tests for the changed code pass and
  the report names them, the documentation it affects is correct, and the
  change is on `main`. Where it can affect placement or routing, rerun the
  affected benchmark cells or say that they were not rerun. A landed change
  claims nothing about any board.
- **A benchmark cell passes** when the saved board has no open connection and
  no DRC error beyond the errors of its unrouted input, under the board's own
  rules, unrelaxed. This is a measure for comparing tools. It is not a
  release.
- **A board is ready for fabrication** is a separate claim about one saved
  board. Do not call a board clean while it carries violations, including
  ones its source design already had. List every remaining ERC and DRC
  violation and every track or via below the size of its net class; native
  DRC does not report the second kind. Say which electrical checks ran
  (netlist parity, topology, simulation) and which placement requirements
  were measured on the final component coordinates (grouping, connector
  edges, keep-outs, decoupling distance). List the supplier and physical
  checks that were not done.

Passing ERC and DRC, or complete routing, does not establish electrical
correctness or placement quality. No agent review or concurrence is part of
any of these claims unless the owner asks for one.

## Rules

- Put the effort into electronics capability. New work does not add records
  whose subject is the work itself: receipts, seals, custody or retention
  records, review seats, committees, adjudication, grants or admission
  ceremonies. The owner directed on 2026-11-09 that the guidance steering
  agents toward this machinery be removed
  ([decision](docs/decisions.md#direction-2026-11-09-capability-over-machinery)).
- The existing review, campaign and stage-receipt code stays supported as it
  is. Do not extend it, and do not copy it into new work.
- Keep what protects a board or the host: checks that an input or output is
  the file it should be and is not stale; ordinary file locks; native tools
  run in the sandbox with a deadline; resource limits; a failure when a tool
  fails or an output is missing. A tool failure is never a pass.
- If existing machinery blocks an ordinary change, remove the block and say so
  in the commit. Do not remove a supported file format, command or public
  behaviour to do it.
- Do not give a private or in-memory structure a schema string and version,
  and do not add a refusal for an identity or record mismatch that no board
  and no host depends on. File formats that users or other tools read keep
  their versions. An error names the condition that was violated.
- Record the tool versions and commits a run used. Use each dependency at its
  current version, fix that version for a run and record it.
- The "non-negotiable evidence model" text in older documents is
  agent-written, not an owner directive.
- Agents forging results is outside the threat model
  ([owner ruling relayed 2026-10-22](docs/decisions.md#decisions-relayed-2026-10-22-agent-519-quiz-answers-and-agent-509-relays)).
  Do not design against it.
- Never tune a board, example, rule or limit to make one board pass. When a
  board run fails, look for a tool defect first and fix the tool generally. If
  the input is invalid or the case is unsupported, report that plainly; the
  tool should refuse it with a clear message.
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

Focused native tests can run directly on a host with compatible KiCad 10.x,
`pcbnew`, ngspice, libraries and working Bubblewrap. Missing prerequisites skip
by default; set `AGENT_EE_REQUIRE_TOOLCHAIN=1` to make them fail. Native tools
still run in their sandbox with deadlines. The nightly uses the container image.
Tests that use the Rust placement engine or external router also need current
binaries; use the image when the host lacks those dependencies
([installation](docs/installation.md#rust-placement-engine)).

One implementer per change is enough. Ask for a second opinion when you are
unsure about the engineering, not as a ritual.

A tracker comment says what changed, the test result and the next step. Leave
out lists of hashes, record identifiers and verdict strings.

## Where things are

- [README](README.md): what the tool does today and what it does not.
- [docs/README.md](docs/README.md): the manual.
- [GLOSSARY.md](GLOSSARY.md): the terms, including the board outcome terms
  used above.
- [docs/decisions.md](docs/decisions.md): owner decisions. The newest entry
  wins.
- [docs/history/](docs/history/README.md): retired process documents. They are
  records, not instructions.
