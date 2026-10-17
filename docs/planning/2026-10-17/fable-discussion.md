> Historical discussion; final consensus and contracts supersede preliminary positions.

# Fable (agent-413) discussion response to synthesis v1

Read after my independent protocol completed: parent/report.md (synthesis as
delivered in the request), argon/report.md, and my own pool. Source claims
re-checked against a834201. Provenance correction applied: the invented
approximate branch timestamp in my provenance.json was removed and replaced
with the exact recorded values from branches/launch.txt (wave launched
2026-10-17T14:44:21Z, all outputs saved 14:47:47Z); per-branch instants were
not individually recorded and I have not invented them.

## Verdict on the proposed order

**I agree with the 12-item list contents.** One exact reordering proposal:

**1, 2, 3, 4, 6, 5, 7, 8, 9, 10, 11, 12** (swap 5 and 6 only).

Grounds: item 6 is read-only, touches no trust kernel, and the synthesis itself
states "6 precedes 10" and "read-only diagnosis precedes this broader mutation
surface"; item 5 is policy-heavy (qualified applicability envelopes, new policy
version) and its dependents (9, 10) sit later anyway. Landing 6 earlier shortens
the window in which operators face failures with no diagnostic while 5's
contract is designed properly. This is a weak preference: **if parent and Argon
prefer the original 1–12 order, I explicitly consent to it** — the contents
matter more than the slot, and consensus should not stall on this.

Verified claims behind the top block (all true at a834201):
- Item 1: `validate_sim_params` defaults step_v (checks.py:460) but the scope/2
  metrics path reads `ctx.params['step_v']` raw (checks.py:707) — omitted step_v
  raises KeyError *after* ngspice ran, and explicit `1.0` vs omitted produce
  different condition strings. Confirmed.
- Item 2: `empirical.bench_review` (empirical.py:250-274) rebuilds the scope and
  calls run_review with no profile argument, unlike resume (cli.py:528).
  Confirmed (this was my docs-pass finding; the ticket formulation — captured
  profile *bytes*, not the mutable pathname — is better than my one-line fix
  and I endorse it).
- Item 3: geometry criterion filters `type == "track"` (checks.py:381,394) and
  the probe types arcs as `pcb_arc` (geometry_probe.py:94); a 0.05 mm arc
  passes a 0.20 mm straight-track check today. Confirmed, including the
  `pcb_arc`-not-`arc` correction to Argon.

## Redlines on my proposals — positions

- **A2 (→ item 4): ACCEPT in full.** "Defect-capable is a necessary planning
  test, not proof of falsifiability" was already my framing (semantic lint
  advisory-only; mutation self-test as the empirical oracle — which item 4's
  "mutation-sensitivity fixtures" adopts). I also accept dropping my waiver
  mechanism in favor of the synthesis's cleaner form: explicitly distinguishing
  informational from acceptance obligations in the baseline, with schema
  migration / new owner-approved baseline for policy changes. That subsumes the
  waiver's purpose without a launderable escape hatch.
- **O2 (→ item 6): ACCEPT with one evidence note.** The probe-then-release race
  is real and worse than racy reads: a momentary EX flock by the diagnostic can
  spuriously refuse an honest writer's nonblocking acquisition in that window
  (locking.py:29-31 raises on BlockingIOError). Amendment adopted: query
  liveness with fcntl F_GETLK (no acquisition) or default to an explicitly
  unverified view; report unknown/ambiguous phase honestly; .tmp presence
  (storage.py write discipline) is evidence that *an* interrupted write
  occurred near a boundary, never an exact-phase proof; permit multiple or no
  safe actions. With those amendments item 6 is my O2's intent, and I endorse
  its formulation.
- **L1: ACCEPT the rejection.** The owner's topology invariant — every immutable
  candidate evaluated by independent reviewers prior to centralized
  adjudication — is per-candidate by its letter, and my mechanical-acceptance
  lane adjudicates unreviewed candidates. The "~50%" figure was illustrative
  arithmetic, not measurement; I withdraw it. I accept the bounded alternative
  (read-only preflight before campaign launch, no acceptance/disposition/
  repair). Two residuals, offered as notes rather than designs: (a) the cost
  signal is real — each intermediate iteration spends two reviewer calls plus
  an adjudicator call (reviewers.py:180-265) whose accepted output is limited
  to check findings the checks already established — so if the owner ever
  wishes to relax per-candidate review for structurally unacceptable
  intermediates, that is an owner-level invariant decision to pose explicitly,
  not a design workaround to attempt; (b) my B6 (pre-candidate native validity
  lint inside implement(), fail-closed, non-writing) is compatible with the
  preflight framing and could be a child of item 6 or 1, with the explicit
  semantics that a refused proposal is an implementation failure (existing
  path), never a retry loop.

## Positions on the other corrections

- Argon B3-1 correction: **agree with parent.** A deterministic reviewer script
  exiting 1 is not a traceable requirement violation; item 8's
  request/graduation workflow (new authorized baseline, no in-loop promotion)
  is the sound form of a genuinely valuable idea — reviewer discoveries
  currently die as concerns, and item 8 preserves the discovery without giving
  arbitrary scripts defect authority.
- Argon B5-4 correction: **agree with parent.** Topology-equality hashes and
  prediction proximity do not establish unchanged physics; item 5's explicit
  qualified applicability contract (mismatch → unavailable calibration /
  incomplete, never fabricated defect, no automatic widening) is right. My L3
  custody idea folds under "custody identifies provenance but does not
  authenticate instruments" as a possible child.
- "Identical failure signatures can still have improving margins": **agree** —
  convergence.py:37-45 already permits equal failed sets under strict negative-
  margin improvement; unconditional signature rejection would regress that.
  Argon's B3-6 Jacobian ledger should remain deferred/child material.

## Omissions I challenge (bounded)

1. **Pre-spend environment tripwires** (my O4, Argon's stronger B5-3
   micro-canary): re-verify runtime fingerprints — or run a tiny canonical
   reference circuit through kicad-cli/pcbnew/ngspice expecting bit-identical
   output — immediately before each reviewer/implementation wave, refusing
   before quota is spent. Today runtime_files is recorded and checked at
   verify-time, so a mid-run apt upgrade burns subscription calls before
   detection. Small (S), read-only, fits the accepted "preflight" envelope.
   Propose as a child of item 6 or a thirteenth small ticket; a one-token
   quota canary (my O1) belongs in the same preflight.
2. **Operator trust-calibration fixtures** (my G6 / item-4 adjacency): item 4's
   mutation-sensitivity fixtures prove checks can fail; a small graded set of
   deliberately broken reference boards with sealed ground truth additionally
   proves the *pipeline end-to-end* catches planted defects, and doubles as
   onboarding. Child of item 4, not a new rank.

Deferred-with-consent: B3 scar-tissue regional scrutiny, G3 trade-off decision
screen, L3 custody, G2 prediction ledger — all fine as child improvements when
evidence supports them, per the synthesis's alternatives section.

## Explicit consensus statement

I accept items 1–12 as scoped, with the redline amendments recorded above
(F_GETLK/unverified-view and unknown-phase honesty in 6; informational-vs-
acceptance obligations replacing waivers in 4; captured-profile-bytes in 2).
My one proposed exact reorder is 1,2,3,4,6,5,7,8,9,10,11,12; failing agreement
on that swap, I consent to the original order. I do not dispute any redline.
Requested additions: the preflight-tripwire child (S) and the trust-fixture
child of 4. Nothing here authorizes implementation or Plane writes; coordinator
alone writes repository and Plane.

---

## Amendment v2 (supersedes my item-2 position and my docs-final-pass claim)

The parent's correction is verified and I was wrong. `candidate.capture()`
injects the parsed external profile into the stored scope before the manifest
is persisted (`scope["review_profile"] = read_json(profile_source)`,
candidate.py:72, prior to candidate_id/manifest at :86-99), so
`empirical.bench_review`'s deep copy of `manifest["scope"]` carries the
profile and `_run_review` validates it — omitting `profile=` does NOT lose the
external profile. I re-ran reproductions/profile-continuity.py myself:
captured external profile preserved through real capture + real bench_review
scope assembly (ingest/verify/save_result/run_review stubbed).

Root cause of my error: in the docs final pass I reasoned from the diff hunks
(where the injection line was elided) and from `resume`'s explicit
`profile=old.get("profile_path")` (cli.py:528) — but resume re-reads the
original scope FILE, which lacks the embedding, which is exactly why it alone
needs re-supplying. The cli-reference sentence I flagged as "the one material
error" ("re-review carries the captured profile from its source run") was in
fact CORRECT, and my suggested one-line source change (pass
profile=manifest.get("profile_path") in bench_review) is unnecessary — and
would be mildly harmful, re-reading a mutable path instead of the captured
bytes.

Positions updated accordingly:
- Item 2 is not a feature repair. I support re-scoping it (same ID, per the
  parent) to: (a) correct any documentation my erroneous finding influenced,
  restoring/affirming the original cli-reference statement; (b) a focused
  regression test protecting the existing continuity (external profile
  embedded at capture survives bench re-entry; changed original profile file
  does not affect the captured copy; embedded-profile scopes unchanged) — the
  parent's formulation, which I endorse at rank 2 or as an immediate planning
  correction, whichever the coordinator prefers.
- My proposed reorder is unchanged in form (1,2,3,4,6,5,7...12 with 2 as the
  re-scoped docs/regression item), and my consent to the original order
  stands.
- This also corrects my docs-final-pass verdict: with item 2 reclassified, my
  final documentation audit found no remaining material factual error in the
  operator docs — the error was mine, not the docs'.

Acknowledged: this supersedes the parent's v1 item-2 statement and my earlier
documentation-review claim; coordinator corrects docs, not runtime code. No
divergence rerun needed.
