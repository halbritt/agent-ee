# agent-ee

The language of agent-ee, a tool that turns engineering requirements into a manufacturable KiCad board. Many terms below name the evidence and review machinery of earlier campaigns; they describe existing code and are not a model for new work (see `AGENTS.md`). Terms here name concepts, not modules; `docs/architecture.md` and `docs/decisions.md` say how and why they are built.

The _Avoid_ lists name loose synonyms to keep out of prose. Existing identifiers, schema names, file names and document titles keep their names; this glossary never authorizes a rename.

## Capture and evidence

**Candidate**:
An immutable copy of the selected source files, identified by its scope and file hashes, that every check, review and judgment is bound to.
_Avoid_: working copy, input set

**Capture**:
The act of producing a candidate from explicit sources, hashing each file and re-checking the live source for races (`placement_problem.capture` is the placement problem builder's solving entry point on an in-memory board description, not candidate capture).
_Avoid_: ingest

**Baseline**:
The sealed requirements, criteria and source identities a candidate is judged against; it does not change during a run.

**Run record**:
The durable result of one run: its outcome, the freshness of its inputs and runtime, and the projections derived from its checks and reviews.
_Avoid_: work record

**Retained run**:
A completed or interrupted build run kept on disk with its scope, accounting, completed stages, review and qualification, readable by later stages and by review.

## Checks

**Check kind**:
One named native or computed verification (DRC, netlist parity, fab consistency, …) with a declared capability, prerequisites and a check record.
_Avoid_: check type

**Check record**:
The persisted outcome of one check kind on one candidate: execution state, findings, limitations and evidence paths.

**Native run**:
One invocation of a native tool (KiCad, pcbnew, the router) under a declared isolation, deadline and mount set, yielding one execution record.

**Execution record**:
The provenance of one native run: argv, isolation profile, exit code, duration and console digest.

## Build and qualification

**Build**:
One captured, resumable path through the stages (requirements, parts, scaffold, schematic, placement and routing, artwork, package) on explicit inputs. "Job" is reserved for the placement job.

**Build admission**:
The refusal or acceptance of a build's inputs before any run directory exists; each input owner admits its own source.

**Stage receipt**:
The sealed evidence that one build stage ran on identified inputs and produced identified outputs.

**Stage execution**:
The execution harness (`stage_execution.py`) that enforces candidate read-only confinement, manages scratch directory lifecycles, and verifies process mountinfo isolation.

**Build completion**:
The reusable typed completion workflow (`build_completion.py`) that replays production verification, validates judgment seat policies, and verifies package binding without coupling to campaign recipes.

**Qualification**:
The assessment of a retained run against sealed typed evidence, producing one qualification record that says whether the run is accepted and why.

## Placement and routing

**Placement job**:
One admitted, deadline-bound run of the placement engine on a whole board: capture, solve, materialize, check, route, judge and promote.
_Avoid_: P run, placer invocation

**Placement intent**:
The declared engineering constraints on placement (fixed poses, edge connectors, proximity, groups, rails) in its normalized form; the only form later steps see.

**Placement problem**:
The engine's input document for a board: geometry, intent, groups, rails, rules and weights; built in two builds through one shared builder (`placement_problem._document`) — `placement_problem.capture` for solving (not the candidate **Capture** above) and `placement_problem.evaluate` for final-board evaluation.
_Avoid_: problem JSON

**Candidate pose**:
One proposed position, rotation and side for a footprint, carrying its provenance and the constraints it was judged against.

**Router attempt**:
One attempt by the kicad-route checkout on a candidate, with preflight, run and one classification of its result as accepted, policy-failed or refused. The attempt records the router commit it used.
_Avoid_: router call, KRT run, pinned router run

**agent-ee.local-routing-seed/1**:
A normalized data-driven schema for local decoupling loops, return paths, and ground-tie geometry derived from board geometry models rather than single-board heuristics.

**Router version**:
The kicad-route commit an attempt ran, fixed for that attempt and recorded in its report. agent-ee names no required commit; before 2026-11-03 a committed table did, and that table is retired (`docs/routing-adapter.md`).
_Avoid_: pin

## Board outcomes

The terms results are reported in (`AGENTS.md`, `docs/benchmarks/`).

**Open connection**:
A pair of pads on one net that the saved board does not join with copper; KiCad reports it as an unconnected item.
_Avoid_: unrouted net (a net can be partly routed)

**The board's own rules**:
The design rules the board's project files state, unrelaxed. A board without a project file has KiCad's defaults, or stated fabrication rules that the report names.

**New error**:
A DRC error on the output board that its unrouted input did not have, matched by type and by the descriptions of its items.

**Remaining violation**:
Any ERC or DRC violation on the saved board, new or inherited from the source design. A board with remaining violations is not clean, whatever the benchmark verdict.

**Benchmark cell**:
One tool configuration run on one prepared board. It passes with no open connection and no new error under the board's own rules. A pass compares tools; it does not release a board.

**Below-class feature**:
A track or via narrower or smaller than its net class asks while still at or above the board's minimums. Native DRC does not report it, so it is counted beside the verdict.

**Placement quality**:
How well the final component coordinates meet the placement request: grouping, connector edges, keep-outs and decoupling distance. Complete routing and clean DRC do not measure it.

**Fabrication release**:
The claim that one saved board can be ordered. It lists every remaining violation and below-class feature, the electrical and placement checks that ran, and the supplier and physical checks that did not.

## Fabrication

**Fab package**:
The staged manufacturing set for a candidate (manifest, BOM, CPL, Gerber and drill outputs) together with its verified reading.
_Avoid_: production files

**Assembly readiness**:
The judgment of whether a candidate's parts, stock, polarity and hand-fit policy allow assembly as captured.
