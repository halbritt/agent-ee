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
mise tasks ls                              # every test and check entry point, with what it runs
mise run test:unit -- test_<module>        # one module or test ID on this host (toolchain required)
mise run test:affected                     # after a commit: the modules the change needs, against origin/main
mise run test:strict -- test_a test_b      # named modules, one process each; a skip fails
mise run test:integration                  # the end-to-end tiers under tests/e2e/
mise run test:engine                       # the Rust placement engine's own tests
mise run test:image -- <command>           # in the CI image, for what this host lacks
mise run check                             # docs, file origins (after git add), conflict markers
mise run doctor                            # toolchain on this host
```

Each task wraps the repository's own command, which `mise tasks info <task>`
shows and which still runs directly, for example
`PYTHONPATH=src:tests /usr/bin/python3 -m unittest test_<module>`. The tasks add
the host's admission and a deadline (the shared `host:exec`), and they require
the toolchain instead of skipping. The strict runner asks the host for one unit
of capacity per worker (`TEST_JOBS`, default 2), so on a busy host
`TEST_JOBS=1 mise run test:affected` starts sooner. Exit 75 with
`native-admit: not admitted` means the host was too busy to start the run, not
that a test failed.

Run the tests for what you changed. `test:affected` selects them with
`scripts/lane_gate_planner.py`: the test modules the change touches, the tests
of what depends on it, and the checks that scan the whole tree. It needs the
change committed. The full suite takes about two hours and runs nightly on the
owner's host ([nightly suite](docs/nightly-full-suite.md)); do not run it for
an ordinary change. It has no task, and a change that selects all of it (the
image's inputs) is refused by `test:affected` with the reason. Use
`git-wt create <slug>` for a worktree, land on `main`, and leave nothing unmerged.

Focused native tests can run directly on a host with compatible KiCad 10.x,
`pcbnew`, ngspice, libraries and working Bubblewrap. Missing prerequisites skip
by default; set `AGENT_EE_REQUIRE_TOOLCHAIN=1` to make them fail. Native tools
still run in their sandbox with deadlines. The nightly uses the container image.
Tests that use the Rust placement engine or external router also need current
binaries; use the image when the host lacks those dependencies
([installation](docs/installation.md#rust-placement-engine)).

Tests that route need `AGENT_EE_KICAD_ROUTE_CHECKOUT`, a clean kicad-route
checkout whose state this process cannot write. The image has one at
`/opt/kicad-route`, built from kicad-route's `main` when the image is built. A
working checkout on the host does not qualify, so with the toolchain required
such a test fails there with `AGENT_EE_KICAD_ROUTE_CHECKOUT is unset`: that is a
missing prerequisite, not a failure of the code. Run those modules in the image:

```sh
mise run test:image -- python3 scripts/strict_suite.py --jobs=1 test_kicad_route_native
```

A few modules read receipts that are not in the repository
(`test_routing_experiment_boundary`, the acquisition modules). The main checkout
has copies under its ignored `build/`; a worktree has none, and the strict
runner refuses the skips. Select the host's inputs as the nightly does, with a
new directory outside the worktree for the runner's record
([container](docs/container.md), "explicit input manifest"):

```sh
AE_CI_INPUT_MANIFEST=$HOME/.local/share/agent-ee/native-inputs/runtime/nightly-manifest.json \
AE_CI_EVIDENCE_DIR=$HOME/.local/share/agent-ee/image-runs/$(date -u +%Y%m%dT%H%M%SZ) \
  mise run test:image -- python3 scripts/strict_suite.py --jobs=1 test_routing_experiment_boundary
```

A change in kicad-route reaches these tests when the image is next built
(`AE_CI_REFRESH_HOURS=0` builds it now). The adapter compares what a run records
with what it asked for, so run them against a router change before that change
lands ([routing adapter](docs/routing-adapter.md)).

`test:image` mounts this checkout in the container; the tests run on the files
as they are, not on a copy. While a run is using a checkout, do not edit it, land
onto its branch or run Python in it on the host: a `.pyc` written under `src/`
is a change the stage checks refuse (`live_package_source`). Use a second
worktree for anything else. One strict suite runs on the host at a time; a
second one waits for it.

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
