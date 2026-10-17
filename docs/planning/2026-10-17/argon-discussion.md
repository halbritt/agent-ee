> Historical discussion; final consensus and contracts supersede preliminary positions.

# Argon (`agent-414`) Cross-Participant Discussion & Consensus Response

## 1. Explicit Delivery-Order Verdict (`1`–`12`)

After reading [`parent/report.md`](parent-exploration.md), [`fable/report.md`](fable-exploration.md), and [`argon/report.md`](argon-exploration.md) and verifying every claim against `src/agent_ee/` and `docs/` at commit `a834201`:

- **Explicit Order**: **`1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12` (Agreed in exact order `1–12`, with one mandatory source-accuracy scope amendment on Item 2; if Item 2 is not reframed around external-profile provenance and test coverage, drop Item 2 under the "No ticket for a falsely claimed absence" rule and advance `3..12` to `2..11`).**

### Why this sequence is right
1. **Items 1–5** close verified correctness, primitive-coverage, requirement-capability, and empirical-applicability holes before expanding automation.
2. **Item 6** provides a strictly read-only forensic diagnostic before any write-side recovery surface (**Item 10**) is touched.
3. **Items 7–9** harden engineering closure (owner-declared hardware interface invariants, structured evidence requests + non-authoritative sandbox witness replay receipts with explicit baseline graduation, and a qualified passive RC/RLC tolerance-corner adapter).
4. **Item 10** makes `bench-review` / `campaign-feedback` ingestion + re-review launch transactionally recoverable without re-running physical measurements or reusing stale model verdicts.
5. **Items 11–12** add topology-indexed packet navigation and bounded DC copper resistance / IR-drop back-annotation once qualification and interface contracts are in place.

---

## 2. Concrete Source Challenge & Bounded Design Amendments

### Challenge 1 — Item 2 ("`empirical.bench_review` currently omits external profile propagation") is contradicted by current source and `docs/documentation-review.md`
- **Source Evidence**:
  - In `src/agent_ee/candidate.py:70-73`, `capture(scope_path, run_dir, profile_path)` reads the external `--profile` file and embeds its parsed JSON object directly into `scope["review_profile"]` before writing `run_dir / "candidate.json"` (`candidate.py:86-98`).
  - In `src/agent_ee/empirical.py:267-273`, `bench_review()` loads `manifest = read_json(run / "candidate.json")`, deep-copies `manifest["scope"]` (which already contains the embedded `review_profile` dict), writes `out / "scope.json"`, and invokes `run_review(out / "scope.json", review_out, max_seconds, previous=str(run))`.
  - Furthermore, `docs/documentation-review.md:78-84` already recorded this exact correction: *"2. **Correction:** standalone `bench-review` preserves external profiles too. `capture` embeds their parsed content in `candidate.json` under `scope.review_profile`; `bench_review` copies that scope into the new review... The earlier missing-profile claim was a source-reading error."*
- **What is *actually* missing in `candidate.py` / `empirical.py` (Bounded Amendment for Item 2)**:
  1. When `capture()` runs with `profile_path`, it stores `profile_path` and `profile_sha256` at the top level of `candidate.json` (`candidate.py:89-92`), and `verify_source()` (`candidate.py:121-122`) checks `digest(m["profile_path"]) != m["profile_sha256"]` against the live external path on disk.
  2. When `bench_review()` writes `out / "scope.json"` and runs `run_review(out / "scope.json", review_out, ...)` without `profile=...`, `review_out / "candidate.json"` keeps the embedded `scope["review_profile"]` content (so subscription routing works even if the original external file was modified), **but** `review_out / "candidate.json"` drops `profile_path` and `profile_sha256` provenance, while `verify(run, current_source=True)` on the source run fails if the external file at `profile_path` was edited or moved after capture.
  3. `tests/test_empirical.py` has zero tests for `bench_review()` profile continuity, mutated original external profile file, or provider-unavailable fail-closed behavior.
- **Proposed Amendment**: Reframe Item 2 from *"fix missing external profile propagation in `empirical.bench_review`"* to: **"Capture external reviewer profile bytes/digest as immutable snapshot provenance across `capture`, `verify_source`, and `bench_review` (decoupling post-hoc verification from the mutable external pathname), and add regression tests for embedded/external profile continuity, mutated original profile file, unavailable provider, and no fallback."**

### Amendment 2 — Item 1 (`rc_transient` `step_v` default & unbounded metric empirical refusal)
- **Source Evidence**:
  - `src/agent_ee/checks.py:460` (`validate_sim_params`) already computes `step_v = float(params.get("step_v", 1.0))` and returns `(step_v, stop_ms, wanted)` at line 492.
  - `src/agent_ee/checks.py:503` (`build_deck`) receives `step_v` and formats `PWL(0 0 1n {step_v:g})` at line 561, but returns only `deck, elements, ignored, excluded_dnp, nodes, wanted` at line 568.
  - `src/agent_ee/checks.py:707` then evaluates `f"ideal-unloaded-{ctx.params['step_v']}V-step-at-{m['at_ms']}ms"`, which raises `KeyError: 'step_v'` after `ngspice` finishes whenever `step_v` was omitted and `measurements` is non-empty.
- **Bounded Amendment**: Return `step_v` from `build_deck()` and format the metric condition as `f"ideal-unloaded-{step_v:g}V-step-at-{m['at_ms']:g}ms"` so omitted `step_v`, `step_v=1`, and `step_v=1.0` all produce the identical canonical condition string `"ideal-unloaded-1V-step-at-<at_ms>ms"`. In addition, because `checks.py:724-728` only calls `measurements.finish()` when `limits` is non-empty, `empirical.ingest()` (`src/agent_ee/empirical.py:141-149`) should explicitly refuse bench observations targeting an unbounded `rc_transient` metric rather than silently ignoring them during `calibrate()`.

### Amendment 3 — Item 3 (`check_geometry` primitive classification & `pcb_arc` identity)
- **Source Evidence**:
  - `src/agent_ee/geometry_probe.py:81-100` iterates `board.GetTracks()`: for `PCB_VIA` it emits `type: "via"`; for `PCB_TRACK` it emits `type: "track"`; for `PCB_ARC` (`cls = "PCB_ARC"`) line 94 emits `type: cls.lower()` = `"pcb_arc"` with `width_mm`, `start_mm`, `end_mm`, `layer`, `length_mm`, and `uuid`.
  - `src/agent_ee/checks.py:381,394` filters `t["type"] == "track"`, silently ignoring `"pcb_arc"` and any unknown primitive classes.
- **Bounded Amendment**: In `check_geometry()`, classify `geo["tracks"]` into `width_checked_types = ("track", "pcb_arc")`, `non_width_types = ("via",)`, and `unsupported_types` (any other `t["type"]`). Record `covered_counts_by_type`, `excluded_net_count`, and `unsupported_counts_by_type` in `ctx.result["details"]`; if `limit is not None` and `unsupported_types` is non-empty, raise `CheckError("unsupported", ...)`. Keep via drill/annular-ring and zone fill checks out of `min_track_width_mm` as separate future criteria.

### Amendment 4 — Items 5, 6, and 10 (Explicit handling of the `bench_review` supersession-before-review window)
- **Source Evidence**:
  - In `src/agent_ee/empirical.py:257-273`, `bench_review()` creates `out` (`exist_ok=False`), runs `ingest(run, report, out / "empirical")`, writes `run / "events/bench-<sha>.json"`, updates `run / "result.json"` (`save_result(run, original)`), and then calls `run_review(out / "scope.json", review_out, ...)`.
  - Once `original["empirical_feedback"]` is non-empty, `cli.py:108-109` (`outcome()`) forces `run`'s outcome to `"incomplete"` (durable supersession). If `run_review` fails or is interrupted, re-running `bench-review` or `campaign-feedback` with the same `out` path fails on `out.mkdir(parents=True, exist_ok=False)`.
- **Bounded Amendment**: Ensure **Item 6** (read-only diagnostic) explicitly detects and reports this state (`empirical_feedback` event committed on `run`, while `review_out` is missing or `incomplete`), and ensure **Item 10** (recoverable bench-feedback transaction) allows idempotent resumption of the pending `review_out` launch when `out / "empirical/calibration.json"` already matches the committed event's `bundle_sha256`.

---

## 3. Explicit Dispositions on All Coordinator Redlines

1. **Redline on Argon `B5-4`** (*"parameter-stripped topology and nominal prediction distance do not establish unchanged physics; material/layout/value changes matter. No automatic envelope widening; invalid applicability is missing evidence, not physical defect."*):
   - **ACCEPT WITH EVIDENCE**. In `src/agent_ee/cli.py:119-126`, `outcome()` separates missing/invalid evidence (`execution != "ok"` or `outcome == "unknown"` $
ightarrow$ `"incomplete"`) from verified hardware violations (`outcome == "defect"` $
ightarrow$ `"defects_or_unresolved"`). First-slice empirical applicability (**Item 5**) must bind exact captured mutable design file hashes (or declared design dependencies) and fail closed to unavailable calibration / `incomplete` evaluation on mismatch—never fabricating a physical `defect` or auto-widening uncertainty intervals.
2. **Redline on Argon `B3-2`** (*"owner declarations determine interface preservation; do not universalize initial design or short R/C/L for equivalence. Native probe emits `pcb_arc`, not `arc`. Geometry and interface contracts are separate deliverable slices."*):
   - **ACCEPT WITH EVIDENCE**. Verified in `src/agent_ee/geometry_probe.py:94` (`cls.lower()` produces `"pcb_arc"`). The initial candidate may itself contain a buggy connector pinout, and two-pin passives (`R`, `C`, `L`) have frequency-dependent impedance that is not equivalent to a wire. Separating **Item 3** (`pcb_arc` width + primitive coverage) from **Item 7** (owner-declared hardware interface invariants in immutable baseline/scope) is sound.
3. **Redline on Argon `B3-1`** (*"deterministic script replay is not a traceable requirement violation. A script can read candidate and exit 1 arbitrarily. Preserve concern and request/graduation workflow, but never directly accept its assertion as a coordinator defect."*):
   - **ACCEPT WITH EVIDENCE**. Verified in `src/agent_ee/baseline.py:137-170` (`validate_witnesses`) and `src/agent_ee/findings.py:607-625`: in `agent-ee.scope/2`, only owner-declared `check/<id>` findings bound to a baseline requirement can be `accepted`. Replaying a reviewer experiment (**Item 8**) provides a retained diagnostic receipt (`reproduced`, `unreproducible`, `harness_error`, `unsupported`) and structured evidence/graduation request, without granting reviewer scripts defect authority or mutating `scope.checks` in-loop.
4. **Redline on Argon Factual Corrections** (*"actual functions `ingest`/`run_review`/`adjudicate`/`executor.execute`; schema `agent-ee.calibration/1`; native connectivity under `work/netlist`; reviewer experiment files already retained; passive simulation refuses unsupported component classes; parser sealing and margin history already implemented. No ticket for a falsely claimed absence."*):
   - **ACCEPT WITH EVIDENCE**. All six source facts are verified in `src/agent_ee/empirical.py:19,104`, `src/agent_ee/cli.py:53-55,259`, `src/agent_ee/executor.py:14`, `src/agent_ee/checks.py:344,534-540,663`, `src/agent_ee/baseline.py:56-58`, `src/agent_ee/campaign.py:58-61`, and `src/agent_ee/convergence.py:18-45`. (And by the same rule *"No ticket for a falsely claimed absence"*, Item 2's description must be amended as noted in Challenge 1 above because `candidate.py:72` + `empirical.py:268` already propagate external profile content into `bench_review`.)
5. **Redlines on Fable `A2`, `O2`, `L1` and `All`**:
   - **ACCEPT `A2` redline (Item 4)**: `defect_capable` is a structural check-configuration audit, not a proof of physical falsifiability; informational checks (`netlist`, unbounded `geometry`) remain valid informational checks, and waivers cannot silently satisfy acceptance requirements.
   - **ACCEPT `O2` redline (Item 6)**: Read-only diagnostics must not perform racy lock probes, mutate files, or guess exact crash phases from `.tmp` artifacts; ambiguous phases must be reported as `unknown`.
   - **ACCEPT `L1` redline**: Skipping independent `circuit`/`physical` reviews on intermediate campaign candidates violates `agent-ee`'s review topology (`campaign.py:151`). Read-only preflight before campaign start is acceptable; checks-only intermediate campaign acceptance/repair lanes are rejected.
   - **ACCEPT `All` convergence/isolation redline**: `convergence.py:37-45` rightly allows identical `failed` check ID sets when negative margins strictly improve without regressing protected margins; unconditional failure-signature rejection (`B3-6`) is rejected.

---

## 4. Provenance Timestamp Audit

All subagent timestamps in `argon/diverge-branches.json`, `argon/focus-branches.json`, `argon/ideas.json`, and `argon/report.md` were cross-checked against the subagent transcript files (`/home/halbritt/.gemini/antigravity-cli/brain/<conversation_id>/.system_generated/logs/transcript.jsonl`) and updated to record both exact timestamps:
- **Diverge (`B1`–`B5`)**: parent `invoke_subagent` step `2026-10-17T14:46:07Z`, subagent transcript first step `2026-10-17T14:46:09Z`, completion steps `14:47:01Z` (`B1`), `14:47:11Z` (`B2`), `14:47:13Z` (`B3`), `14:47:07Z` (`B4`), `14:47:04Z` (`B5`).
- **Focus (`F1`–`F3`)**: parent `invoke_subagent` step `2026-10-17T14:49:01Z`, subagent transcript first step `2026-10-17T14:49:02Z`, completion steps `14:49:49Z` (`F1`), `14:49:50Z` (`F2`), `14:49:43Z` (`F3`).
- **Scoring Protocol Correction**: `2026-10-17T14:59:28Z`.

---

## 5. Incorporation of Coordinator Correction `f79e6e2a-a735-41d3-9813-832723e11168` v1 (Item 2 Premise Change)

- **Acknowledged Premise Change**: Coordinator correction `f79e6e2a-a735-41d3-9813-832723e11168` v1 supersedes the synthesis-v1 Item 2 runtime-defect premise and aligns with our independent source check (`src/agent_ee/candidate.py:70-73` injects parsed `profile_path` JSON into `scope["review_profile"]` before writing `candidate.json`, and `src/agent_ee/empirical.py:267-273` deep-copies that stored `scope` into `out / "scope.json"` for `cli._run_review`, verified by `/tmp/agent-ee-adhd-20261017/reproductions/profile-continuity.py`).
- **Updated Item 2 Definition (Preserving IDs `1..12` at Rank 2)**:
  - **Item 2 (Rank 2)**: **Correct documentation claims regarding external reviewer profile continuity across `bench-review` and add a focused regression test protecting existing `capture()` $
ightarrow$ `bench_review()` profile continuity** (including mutated/removed original external profile file after capture and fail-closed unavailable provider behavior), **without** creating a fake missing-runtime-capability ticket.
- **Explicit Consensus Order (`1..12`)**: **`1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12`** (with Item 2 scoped strictly to docs correction + regression test protection of existing runtime continuity).
