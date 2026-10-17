> Historical independent exploration, before consensus. Proposals and source claims
> here are hypotheses; the final roadmap and its dispositions supersede them.
> These are not implemented capabilities or engineering qualification evidence.

# ADHD run: agent-ee enhancement proposals (Fable, agent-413 — independent phase)

## 1. Brief

Problem as run: *What enhancements/improvements would most improve agent-ee as a
useful, trustworthy electronics engineering review and repair tool?* — preserving
one implementation writer, immutable candidate/requirements, independent initial
reviewers, centralized evidence-backed adjudication, bounded convergence, bench
feedback. No reframe; five parallel isolated frames (attacker, 3am on-call,
logistics, game design, biology-wild) against repo a834201. Full pool, branch
outputs and machine-readable scores: `ideas.json`, `branches/*.json`,
`focus/*.json`, `provenance.json` in this directory.

## 2. Wide set (30 ideas, 6 clusters; chips = [Novelty Viability Fit], weighted .35/.40/.25)

**prove-the-verifier plays** — self-tests and audits of the trust machinery itself
- A2 unfalsifiable-requirement detector [N7 V8 F9] 7.90
- A1 poison-pill capture-boundary canaries [N6 V8 F8] 7.30
- A6 evidence-consumption coverage gate [N8 V5 F8] 6.80 ⚠trap
- G6 broken-reference-board trust onboarding [N5 V8 F7] 6.70
- A4 metric-gaming detector (margin gains must trace to design edits) [N7 V6 F7] 6.60
- A3 reviewer-collusion fingerprinting [N8 V5 F7] 6.55
- B1 reviewer false-positive selection on seeded known-goods [N8 V5 F6] 6.30 ⚠trap(as auto-pruning)
- B2 complement-cascade evidence chain [N4 V6 F6] 5.30
- B4 quorum sensing via shared claim store [N6 V4 F5] 4.95 ⚠trap

**spend-judgment-where-it-counts plays** — route expensive LLM judgment only where it changes outcomes
- L1 customs-lane staged review depth for campaign iterations [N7 V8 F9] 7.90
- B6 chaperone pre-candidate lint (kill doomed proposals before a review iteration) [N7 V8 F8] 7.65 (corrected; originally misprinted 7.75)
- O4 staleness tripwires before each expensive phase [N5 V9 F8] 7.35
- O1 pre-flight quota canary per harness [N4 V8 F8] 6.60
- L4 cross-docked extraction dedup [N5 V5 F5] 5.00 ⚠trap

**fail-legible-resume-safe plays** — failures explain themselves and name the safe next action
- O2 campaign coroner (crash forensics + single safe next action) [N6 V9 F9] 7.95
- O5 refusal explainer (clause + hash + remediation) [N4 V8 F8] 6.60
- O6 sealed resumable partial verdict [N6 V5 F7] 5.85 ⚠trap
- O3 auto kill-and-replace wedged reviewer [N5 V5 F7] 5.50 ⚠trap

**custody-beyond-hashes plays** — physical evidence provenance beyond byte hashes
- L3 cold-chain custody for bench evidence (calibration expiry, quarantine) [N7 V7 F8] 7.25
- B5 hormetic bench dosing (stress epochs with margin-response semantics) [N8 V5 F7] 6.55
- A5 residuals re-derived from raw instrument captures [N6 V6 F8] 6.50

**memory-without-contamination plays** — compounding institutional memory, independence intact
- B3 scar-tissue annotation: repaired regions carry permanent elevated scrutiny [N8 V7 F8] 7.60
- L2 defect depot of failed-iteration counterexamples (implementation-side only) [N6 V7 F7] 6.65
- G5 cross-project defect bestiary as reviewer hunting lenses [N7 V6 F6] 6.35
- L6 per-reviewer requirement-coverage waybills with union audit [N6 V6 F7] 6.25
- G1 replay-ghost margin trajectories [N6 V6 F6] 6.00

**operator-as-player plays** — the engineer as calibrated participant
- G2 pre-registered operator predictions + calibration ledger [N8 V7 F7] 7.35
- G3 trade-off boss-screen (tensions, evidence chains, spend options) [N6 V8 F8] 7.30
- G4 campaign forks with side-by-side history diffs [N7 V7 F8] 7.25
- L5 signature-on-delivery defect acknowledgment at board locations [N7 V6 F7] 6.60

## 3. Converge

Shortlist:
- **O2 campaign coroner** (7.95) — highest viability of the pool; turns the documented 3am manual procedure into one read-only command; zero trust-kernel risk because every verdict comes from the existing verifiers.
- **A2 unfalsifiable-requirement detector** (7.90) — the trust story's foundation: "only failed checks become defects" is vacuous if a requirement can never fail; closes a real structural gap in compile_baseline at capture time, cheaply.
- **L1 customs-lane staged review depth** (7.90) — the dominant cost lever: today every campaign iteration pays 2 reviewers + adjudicator even while checks still fail and repair consumes only accepted check findings; ~50% agent-call savings on a representative campaign with acceptance integrity provably unchanged.
- ★ **B3 scar-tissue annotation** (7.60) — the non-obvious-but-viable pick: repaired board regions are the most likely regression locus; hash-bound region markers that permanently raise check density and reviewer attention there compound campaign history into physical-locus vigilance without contaminating reviewer independence.

Traps (excluded from deepening):
- A6 evidence-consumption gate — attention tracking from harness logs is unreliable; as a hard gate it fails honest runs on instrumentation noise (advisory metric only).
- O3 auto reviewer replacement — a retry by another name; violates the deliberate no-retry/fail-closed provider policy.
- O6 resumable partial verdict — reusing partial evidence undermines the freshness-by-rerun principle (fine as a notarized report, not as resumable acceptance).
- L4 extraction cross-docking — premature optimization: the extractions cost seconds while the dedup complicates the trust chain.
- B4 quorum-sensing claim store — the adjudicator already sees all claims; a shared store risks reviewer isolation for no added signal.
- B1 as auto-pruning — dropping reviewers by scorecard is model substitution, which the explicit-profile policy forbids (keep as a reported scorecard only).

## 4. Focus (three deepened branches; full ticket-grade JSON in focus/)

### O2 — Campaign coroner (`focus/O2-coroner.json`)
Read-only `agent-ee coroner CAMPAIGN_OR_RUN`: classifies the target, probes
writer leases non-persistently, replays the existing verifiers fault-tolerantly
(verify(), a lock-free-refactored verify_campaign body, advance()), uses
write_json's atomic tmp+fsync+replace signature and known crash windows
(campaign.py:117-128, 161-164; cli.py:340-350) to name the phase of death, and
prints per-record valid/stale/missing/partial verdicts plus exactly one safe
next action drawn from the documented command vocabulary — never a state edit.
**Load-bearing risk:** semantic drift if it reimplements rather than reuses the
verifiers; mitigated by a differential test that coroner verdicts match
verify/verify-campaign exceptions on identical trees. **First step:** extract
verify_campaign's body into a lock-free helper; `autopsy(path) -> dict`;
torn-record test. **Children:** `--json` autopsy schema for babysitter loops
(report-only, consistent with no-pre-scripted-resolutions), crash-injection test
harness (doubles as campaign.py's first systematic crash-consistency suite),
opt-in phase tombstones, lock-liveness PID enrichment, unify with `inspect`.
Effort M; risk low (read-only).

### A2 — Unfalsifiable-requirement detector (`focus/A2-falsifiability.json`)
Pure `defect_capable(spec)` classifier colocated with CHECK_FUNCS (netlist
never; geometry only with min_track_width_mm; rc_transient only with a bounded
measurement; measurements/coupled only with nonempty limits; erc/drc always;
unknown kinds never), enforced in compile_baseline after the existing coverage
guard: a requirement is falsifiable iff ≥1 bound check is defect-capable, else
capture refuses with the exact remediation — or carries an explicit, immutable,
baseline_id-bearing `baseline.waivers` entry surfaced in packets as a
limitation. validate_witnesses already makes defect-reachability precisely the
falsifiability condition (baseline.py:162-166). Fully-falsifiable scopes produce
byte-identical baseline.json (no storage change). **Load-bearing risk:**
classifier drift against check implementations; mitigated by an exhaustiveness
test pinned to set(CHECK_FUNCS) and a mutation self-test oracle (child idea).
**First step:** failing vacuous-fixture test + ~15-line classifier. **Children:**
semantic limit-to-quantity lint (advisory), runtime "unfalsified this run"
attestation at seal time, `baseline-probe` mutation self-test, `baseline-audit`
CI surface, scope/1 all-vacuous warning. Effort M; risk low-moderate.

### L1 — Customs-lane staged review depth (`focus/L1-lanes.json`)
Campaign iterations that still fail native checks stop after checks+seal in a
cheap lane (no packet/reviewers/adjudicator) with a new honest outcome
`checks_failed`, lane and risk-manifest recorded; check defects are accepted by
a mechanical coordinator disposition (sound: check defects are un-dismissable
and the only acceptable findings in scope/2, so implement() receives exactly
today's repair targets). Full review runs unconditionally when checks pass, on
the final iteration, on failed-set shape changes, and on HMAC-seeded random
audits whose seed is sealed under config_sha256 and invisible to the
implementation agent; verify_campaign recomputes every lane obligation and
fails closed on mismatches. Acceptance (`criteria_satisfied`) remains derivable
only from full-lane runs — the saving applies only to iterations that could
never be accepted anyway (~50% of agent calls on a representative campaign).
**Load-bearing risk:** lost reviewer coverage-challenge signal on skipped
intermediates (wasted budget, never bad acceptance) plus it lands in the trust
kernel (outcome()/advance()/verify()); mitigated by a pure lane-policy function
shared verbatim between live decision and reverification, and the
convergence-parity and never-accepts tests. **First step:** thread
lane/outcome/convergence kernel changes with round-trip verify() test before
touching campaign.py. **Children:** margin-velocity escalation, single-reviewer
mid lane, concern carry-forward ledger, diff-weighted audit probability,
commit-reveal seed, `review --cheap-until-pass` backport. Effort M (3-5 days);
the highest-risk of the three.

## 5. Provocation

**Give the bench a reviewer's seat.** Today physical reality enters only as
operator-initiated feedback after acceptance. What if a scope could declare a
*hardware witness requirement* — certain requirement classes cannot reach
`criteria_satisfied` on simulation alone, but demand at least one
instrument-sample witness per epoch, making the prototype a standing third
"reviewer" whose findings are samples rather than prose? That inverts bench
feedback from correction-after-the-fact into a first-class acceptance gate, and
would force the measurement/calibration interfaces to grow toward
hardware-in-the-loop review.

## 6. Initial ranked recommendation (for the consensus debate)

1. **A2 unfalsifiable-requirement detector** — the highest trust-per-effort in
   the pool: it hardens the exact property ("a green verdict means every
   requirement was at risk of failing") the whole scope/2 architecture exists to
   provide, in a small, storage-compatible, capture-time change. Confidence: high (0.8).
2. **O2 campaign coroner** — operability is the current adoption bottleneck
   (the guide literally prescribes manual 3am forensics); read-only, zero
   trust-kernel risk, and its crash-injection suite will surface real recovery
   gaps as a side effect. Confidence: high (0.75).
3. **L1 staged review lanes** — the biggest cost/throughput lever and fully
   invariant-compatible on paper, but it modifies outcome()/advance()/verify()
   simultaneously; schedule after A2/O2 and land it with the kernel-parity tests
   first. Confidence: moderate (0.6 on ordering vs B6/B3; high that it belongs
   in the top tier).

Next tier worth tickets: B6 chaperone pre-candidate lint (composes
multiplicatively with L1: cheap native lint kills doomed proposals before even a
cheap-lane iteration), B3 scar-tissue regional scrutiny (★ non-obvious), L3
bench cold-chain custody, G3 trade-off decision screen, O4 staleness tripwires.

Overall confidence that the top three are the right *set* to design first: 0.7;
the ranking within the set is weakly held between A2 and O2 (both small and
high-value) and I expect debate to legitimately reorder L1 against B6.


---
SUPERSEDED NOTE (2026-10-17, final confirmation): B6's weighted score was originally misprinted as 7.75; correct arithmetic is .35*7+.40*8+.25*8 = 7.65. Top-3 set and order unchanged. Item-2-related content: see discussion-response.md Amendment v2 — the bench-profile claim was a false runtime bug (capture embeds the external profile; docs corrected at 5516e13); treat report mentions of that gap as superseded.
