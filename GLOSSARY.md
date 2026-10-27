# agent-ee

The language of agent-ee: a captured, checked, evidence-bound path from engineering requirements to a reviewed board, with explicit refusals at every step. Terms here name concepts, not modules; `docs/architecture.md` and `docs/decisions.md` say how and why they are built.

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

**Pinned router run**:
One attempt by the pinned kicad-route checkout on a candidate, with preflight, run and one classification of its result as accepted, policy-failed or refused.
_Avoid_: router call, KRT run

**Pin**:
The exact kicad-route commit and code hashes agent-ee is qualified against; a run on any other code is refused.

## Fabrication

**Fab package**:
The staged manufacturing set for a candidate (manifest, BOM, CPL, Gerber and drill outputs) together with its verified reading.
_Avoid_: production files

**Assembly readiness**:
The judgment of whether a candidate's parts, stock, polarity and hand-fit policy allow assembly as captured.
