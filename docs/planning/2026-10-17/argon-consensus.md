# Argon (`agent-414`) Final Design & Priority Confirmation

## 1. Explicit Confirmation of Final 12-Ticket Delivery Order & Detailed Contracts

After reviewing [`/tmp/agent-ee-adhd-20261017/final-review-contracts.json`](contracts.json), [`/tmp/agent-ee-adhd-20261017/synthesis-v1.md`](discussion-proposal.md), [`/tmp/agent-ee-adhd-20261017/parent/report.md`](parent-exploration.md), [`/tmp/agent-ee-adhd-20261017/fable/report.md`](fable-exploration.md), and [`/tmp/agent-ee-adhd-20261017/argon/report.md`](argon-exploration.md) against `src/agent_ee/` at commit `a834201`:

- **Confirmed Final Delivery Order (12 Enhancement Tickets + 1 Roadmap Parent)**:
  **`1, 3, 4, 5, 6, 13, 7, 8, 9, 10, 11, 12`**
- **Detailed Contracts Status**: **Explicitly confirmed in full** as specified in [`/tmp/agent-ee-adhd-20261017/final-review-contracts.json`](contracts.json) (`contracts` for IDs `1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13`, ordered `[1, 3, 4, 5, 6, 13, 7, 8, 9, 10, 11, 12]`), with zero material corrections or alternative ordering needed.

---

## 2. Incorporation of Withdrawn Proposal 2 & Resolution with Earlier Discussion

1. **Withdrawn Proposal 2 Incorporated**:
   - Proposal 2 (*Preserve captured reviewer profile across standalone bench re-entry*) was based on a false runtime bug claim in the earlier documentation audit.
   - As verified in `src/agent_ee/candidate.py:70-73`, `src/agent_ee/empirical.py:267-273`, and `/tmp/agent-ee-adhd-20261017/reproductions/profile-continuity.py`, `capture()` already embeds parsed external `--profile` content into `candidate.json["scope"]["review_profile"]` and `bench_review()` preserves it when assembling `out / "scope.json"`.
   - Because the documentation correction is already committed and pushed (`5516e13`), Proposal 2 is completely withdrawn from the runtime enhancement backlog and no Plane ticket is created for it.
2. **Resolution with Earlier Discussion Response (`discussion-response.md`)**:
   - In Section 1 of our earlier discussion response (`discussion-response.md`), we explicitly stated that if Item 2 was not kept as a provenance/test item, we should *"drop Item 2 under the 'No ticket for a falsely claimed absence' rule and advance `3..12`"*.
   - Withdrawing Proposal 2 completely and inserting **Item 13** (*Refuse environment drift before spending review inference*) after **Item 6** cleanly resolves our Challenge 1 while keeping a 12-item enhancement sequence (`1, 3, 4, 5, 6, 13, 7, 8, 9, 10, 11, 12`).
3. **Why `5` Precedes `6` and `13` Follows `6`**:
   - **Item 5 before Item 6**: Item 5 (*Reject empirical correction outside its declared design applicability*) prevents silent misapplication of bench residual intervals across mutable design edits (`campaign.py:131-138`, `empirical.py:28-30,71-101`), which is an active soundness/applicability hazard, whereas Item 6 (*Explain incomplete campaigns without changing their state*) is a read-only diagnostic on already-stopped runs.
   - **Item 13 after Item 6**: Item 13 (*Refuse environment drift before spending review inference*) checks captured tool/runtime/policy identities before reviewer, adjudicator, and implementation calls and emits a retained preflight receipt without spending a model call, without claiming bit-identical numeric output, and without introducing a 1-token quota probe or provider fallback.

---

## 3. Contract-by-Contract Verification Summary (`1, 3, 4, 5, 6, 13, 7, 8, 9, 10, 11, 12`)

1. **ID `1` — Normalize RC step defaults through measured-result generation (`small`, `AFK`, `deps: []`)**: Confirmed. Deck generation and metric-condition construction share one validated parameter object without rewriting input `scope.json` or altering historical identities.
2. **ID `3` — Make copper-width coverage explicit and include native arcs (`medium`, `AFK`, `deps: []`)**: Confirmed. Versioned geometry criterion covers `track` and `pcb_arc` (`PCB_ARC`), reports primitive UUID/type/net/layer/value/limit and eligible/checked/excluded/unsupported counts, fails closed to incomplete coverage on empty/unknown geometry, and keeps via/zone rules separate.
3. **ID `4` — Audit executable acceptance coverage before launching reviews (`medium`, `AFK`, `deps: []`)**: Confirmed. Distinguishes acceptance vs. informational obligations, requires $\ge 1$ defect-capable check per acceptance requirement, includes built-in mutation corpus and full-pipeline scripted reviewer/adjudicator fixtures on deliberately broken reference boards + matched controls, and forbids waiver laundering.
4. **ID `5` — Reject empirical correction outside its declared design applicability (`large`, `AFK`, `deps: []`)**: Confirmed. Versioned calibration bundle binds exact captured design-dependency hashes by default; mismatch prevents calibrated acceptance (`incomplete` / missing evidence) and requests new bench evidence without fabricating physical defects or auto-widening intervals.
5. **ID `6` — Explain incomplete campaigns without changing their state (`medium`, `AFK`, `deps: []`)**: Confirmed. Strictly read-only diagnostic sharing pure verifier semantics, creating no lock files, avoiding `F_GETLK` flock ownership claims, treating `.tmp` as a potentially active write, defaulting to advisory/unverified snapshot unless a coherent lease is held, and permitting `unknown` phase and zero/multiple actions.
6. **ID `13` — Refuse environment drift before spending review inference (`small`, `AFK`, `deps: []`)**: Confirmed. Compares declared tool/runtime/policy identities against captured expectations before reviewer, adjudicator, and implementation calls, emits a retained preflight receipt, makes zero model calls on refusal, makes no bit-identical numerical promise, and performs no 1-token quota probe or fallback.
7. **ID `7` — Enforce declared hardware interfaces across candidate repairs (`large`, `AFK`, `deps: [4]`)**: Confirmed. Enforces owner-authored immutable interface declarations (never inferred from `J*`/`TP*` prefixes or candidate 0), refuses passive-transparent `R`/`C`/`L` shorting, and distinguishes named/anonymous nets.
8. **ID `8` — Turn unresolved concerns into reproducible evidence requests (`large`, `AFK`, `deps: [4]`)**: Confirmed. Binds requests to candidate/baseline/requirement/concern, replays reviewer experiments in a networkless sandbox with separate execution and assertion fields, denies defect authority to arbitrary script exit codes, and exports graduation bundles for a new owner-authorized baseline/campaign.
9. **ID `9` — Ship a qualified passive tolerance-corner adapter (`large`, `AFK`, `deps: [4, 5]`)**: Confirmed. Deterministic corner enumeration for qualified passive topologies with independent analytic RC oracle, explicit qualification manifest, and condition/unit preservation for coupled and empirical checks.
10. **ID `10` — Recover interrupted bench-feedback transactions safely (`large`, `AFK`, `deps: [5, 6]`)**: Confirmed. Versioned journal (`prepared`, `supersession_committed`, `review_started`, `completed`) with directory `fsync` ordering under one writer lease; idempotent retry for identical operation, fail-closed on conflicting identities, and no automatic replay of uncertain external model calls or physical acquisition.
11. **ID `11` — Index reviewer packets by entities and requirement coverage (`medium`, `AFK`, `deps: []`)**: Confirmed. Deterministic entity/requirement/check index and coverage manifest with continuation locators while preserving full `/candidate` and `/evidence/baseline.json` access and strict initial-reviewer isolation.
12. **ID `12` — Qualify PCB DC-resistance back-annotation for declared paths (`large`, `AFK`, `deps: [3, 4]`)**: Confirmed. Deterministic DC resistance extraction (`rho * L / A`) across straight and arc traces + separately qualified via/parallel-path models, feeding declared measurement/coupling criteria and refusing unsupported zones or unmodeled geometries.
