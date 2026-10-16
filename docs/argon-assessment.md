# Independent Assessment (`gemini-argon` / `agent-414`)

## 1. Observations from Inspected Sources (Reusable Pieces vs. Gaps)

### `/home/halbritt/git/fome-mc/toolchain/eda.py`, `versions.toml`, `test_smoke.py`, `rc_filter/generate.py`
* **Reusable validation methods**:
  * Hermetic KiCad 10 config (`ensure_kicad_config`, `tool_env`) and explicit check-status taxonomy (`PASS` / `FAIL` / `NOT_RUN` / `UNSUPPORTED`, keeping tool execution health distinct from design cleanliness and findings).
  * Native `kicad-cli` verification patterns: ERC hierarchy sheet-instance count verification (`step_erc`), DRC saved-vs-refill hash tracking and console-log regex check for silently skipped `--schematic-parity` (`step_drc`), and S-expression / XML netlist export (`step_netlist`).
  * `ngspice` batch runner and binary `.raw` + `.meas` validator (`simulate`, `read_raw`), which supplies a deterministic `.control` block (`set ngbehavior=kicad`), rejects embedded `.control` blocks, and fails on solver/convergence errors, missing `.meas` outputs, or non-finite waveforms.
  * Deterministic fixture generation pattern (`generate.py`: `.kicad_sch` + `kicad-cli sch upgrade --force` + `pcbnew` board construction with `KIID_PATH` links, tracks, vias, and `ZONE` fill).
* **Observed Gaps Addressed by `agent-ee` Architecture**:
  1. *Incomplete candidate identity in `eda.py`*: `make_snapshot` in `eda.py` walked the snapshot without following symlinks and only recorded `git rev-parse HEAD` for `library_dirs`, omitting file hashes of external libraries, stock symbols/footprints, SPICE models, requirements, and review config. `agent_ee.candidate` solves this by requiring explicit input declarations in `agent-ee.scope/1` (refusing symlinks and overlapping destinations) and hashing all captured files and scope into `candidate_id`.
  2. *`Polygonus-Universal-112` library layout*: `fp-lib-table` references `${KIPRJMOD}/wideband_module.pretty` (missing on disk) and `${KIPRJMOD}/fome-kicad-libs/fome_modules.pretty` (uninitialized submodule mapped to `/home/halbritt/git/kicad-libraries`), plus nested library directories (`Libraries/`, `kicad-libraries/`). Missing hierarchy files or module internals must remain explicit coverage gaps, never silent passes.
  3. *Lifecycle persistence and resume*: Runs need explicit per-check and per-review artifact persistence, immutable event history, and freshness checks across candidates.

### `/home/halbritt/git/fome-mc/toolchain/vendor/kicad-happy` (`v2.3.1` @ `06840467ad0f`)
* **Observations**:
  * `deep_review_gate.py` checks whether cited components/nets/pins exist in analyzer JSON, but does not enforce candidate/run/packet identity binding or adjudicate multi-reviewer dispositions without voting.
  * `merge_annotations.py` in `v2.0` removed authority caps so `"suppressed"` annotations unconditionally apply overlays. `agent-ee` instead requires explicit evidence-grounded adjudication (`agent-ee.decisions/1`) with append-only event history and no majority voting.

## 2. Hardest Acceptance Risks & Mitigations

1. **Candidate, Run, and Packet Identity Binding (Stale Evidence Refusal)**
   * *Risk*: Reusing a reviewer output or check result from an earlier candidate, run, or evidence packet, or closing a defect on the same candidate where it was reported without a repair and fresh affected checks.
   * *Mitigation*: `validate_review` enforces exact `candidate_id`, `run_id`, and `packet_sha256` match against the active run and verifies citation paths resolve inside the run's `inputs/`, `checks/`, or `work/<check-id>/` directories (never other reviewer outputs, parent traversals, or symlinks). Closure requires a distinct `candidate_id` (`closed_in_candidate_id != original_candidate_id`) and fresh passing affected checks on the new candidate.
2. **Preserving Singleton Findings & Uncertainty (No Voting)**
   * *Risk*: Majority clean reviews erasing a valid singleton finding, or duplicate links hiding an unresolved/accepted defect.
   * *Mitigation*: `collect_findings` preserves every reviewer claim (`<role>/<id>`) and every check defect (`check/<id>`) with `status="unresolved"`. Duplicate linking (`disposition="duplicate"`) requires an existing non-self target, rejects duplicate cycles, and refuses to mark a finding as `duplicate` of a `dismissed` target (or otherwise hide an unresolved/accepted defect).
3. **Separating Structural Citation Validity from Electrical Proof & Downgrading Unsupported Defects**
   * *Risk*: Reviewers claiming `assessment="defect"` without any evidence citations, or citing nonexistent/traversal/reviewer-output paths.
   * *Mitigation*: Reject malformed schemas and invalid/unsafe citation paths (`ValueError`). When a reviewer claim has `assessment="defect"` with empty `evidence`, visibly downgrade `assessment` to `"concern"` (recording the downgrade in finding metadata/notes) rather than accepting an ungrounded defect or silently dropping the concern.
4. **Honest Check & Execution Statuses**
   * *Risk*: Treating `failed`, `timeout`, `unavailable`, `malformed`, `not_run`, `interrupted`, or `unsupported` check executions as clean passes, or allowing closure against non-passing checks.
   * *Mitigation*: Keep `execution` and `outcome` orthogonal (`execution="ok"` with `outcome="defect"` is a detected violation; non-`ok` execution or `outcome="unknown"` is a coverage gap/incomplete check, never a pass). Closure validation requires affected checks in the new candidate to have `execution == "ok"` and `outcome == "pass"`.

## 3. Disjoint Implementation Ownership (`agent-414` / `gemini-argon`)

* **Owned files**:
  * `docs/argon-assessment.md` (this document)
  * `src/agent_ee/findings.py`
  * `tests/test_findings.py`
  * `docs/contracts-argon.md` (only if signaling API refinements/details to Coordinator and Fable)
