# AGENTEE-492: exact CAD RF measurement checkpoint

2026-10-23, agent-510. **HOLD: measurement contract not implemented.** This is the
explicit obstacle-and-finite-remedy return permitted by AGENTEE-492, not a passing
checker or a manufacturing acceptance. No production code changed. Agent-449 owns
integration; the branch is delivered for the scheduled independent review.

## Frozen inputs and scope

- agent-ee `254d259d572d730ecc764012363bfdbe0390a557`.
- `src/agent_ee/rf_geometry.py` SHA256
  `46975c4fe3cfbac14bd3906c3bfad4cf8e3f399d671e8416634367a95675c7e1`.
- Dispatch `94716639-67b8-4321-9a42-3db381cd998d@1`; AGENTEE-492 is governing.
- Exact emitted width: **293000 nm**. Exact nominal gap: **203200 nm per flank**.
  Inverse-model width 0.29286522460874 mm is historical model output, not the CAD
  comparator. No solver call, tolerance band, geometry exception or source edit.
- Native diagnostic image `agent-ee:exec-149276250b0861ec`, ID
  `sha256:746a046f13cb1c7987f42b1187818cf19da2d209a7d84308a3d23a214070624d`;
  mounted frozen Python source, KiCad version retained in `result.json`.
  This image is a diagnostic runtime, not a new production compatibility claim.

## Reproduced obstacles

`probe.py` creates nine native saved boards from `tests/rf_geometry_fixture.py`.
It records native extraction and evaluation. Its **zero-parser-bypass** branch is
an intentional diagnostic of `evaluate_cpw`, never an accepted declaration.

| Control | Observed result |
| --- | --- |
| Zero gap tolerance | `parse_declaration` refuses `gap_tolerance_mm must be positive` |
| Negative / bool / NaN gap tolerance | Refused; preserve these refusals |
| Native width .293, with parser bypass only | Width PASS at zero tolerance |
| Native widths .292 and .294 | Width DEFECT at zero tolerance |
| Nominal, low .18 and high .23 gap | Gap UNKNOWN at zero; no control can prove exact-mode success |
| No coplanar ground pour/tracks | Structure DEFECT, even though ground vias remain |
| No reference plane | Reference DEFECT; via annuli do not cover the strip |
| Retained In1 fill, all vias removed | Reference PASS despite no through-layer physical connection |
| Straight/45-degree/arc native geometry | See measured deviations below; one global error bound cannot identify their causes |
| Direct evaluator given a different RF net | Not refused for the via-free report: report does not bind net names |

The last row is a **report-boundary weakness**, not a claim that the normal
`check_rf_geometry` process selects the wrong net: its probe request uses the
resolved net and its saved-board hash is checked. Explicit request identity is
still needed to reject a valid-shaped stale/wrong-net report on the same board.

### Saved filler geometry is not serialization noise

At requested clearance .2032 mm, ordinary axis-aligned samples measure .2037 mm:
a **500 nm positive gap displacement**. The existing project already names this
KiCad filler margin as `kicad_route_attempt.FILLER_EXTRA_CLEARANCE_MM = .0005`;
see also `docs/routing-adapter.md`. The native result corroborates that value.
The 45-degree sample is 500.290236888 nm above nominal. The arc's two midpoint
flanks differ by approximately 3168.247336 and 4633.454017 nm. The latter are
observations, **not established approximation bounds**. Native `max_error_nm` is
5000; the RF centerline chord sagitta setting is separately 100 nm.

Some raw midpoint samples hit junction/launch surroundings and have much larger
gaps. The probe labels these raw measurements; they are not claimed to be uniform
cross-section acceptance samples. Existing evaluation has separate transition
skips. Do not tune a tolerance to the largest number in this fixture.

500 nm is not float roundoff or a manufacturing spread. Calling the emitted
.2037 mm gap equal to .2032 mm would relax the exact CAD policy. Nor may this task
silently reduce clearance to .2027 mm to compensate: required DRC clearance must
remain intact. A source policy alone does not prove the desired copper was emitted.

### Information discarded before comparison

`extract` unions ground tracks, pads, graphics, and filled-zone polygons, then
clips them. It retains only a board-wide `max_error_nm`. It loses primitive/edge
identity, polygonization direction, which edges were exact straight boundaries,
and which boundaries resulted from fill/clipping. `track_polyline` separately
approximates arcs. A single `abs(measured-target) <= max_error` cannot distinguish
real excess gap from either approximation, and is therefore not a valid equality
certificate. `max_error=0` on a report would not repair these omissions.

`evaluate_cpw` samples maximum gap at DEFAULT_STEP_MM=.05 and computes a minimum
over the polyline. A widening between samples can be missed. It counts transition
samples but does not locate their extents; it permits an overall gap PASS with
ungraded transitions. That historical behavior is not sufficient for a new exact,
no-exceptions policy.

Reference copper is selected by **net label** and geometric containment. The
native probe removes every via after filling and saving; all fixture ground pads
are F.Cu SMD. In1 retains one filled polygon with no conductive path to a terminal.
`evaluate_cpw` reports reference PASS. Independent Fable review reproduced this
with a freshly filled `vias=False` board too: this is not limited to stale fill.
The existing composed pod-shaped declaration already reports a stitching DEFECT
when all vias are absent; a CPW-only declaration lacks that safeguard. A reference
island disconnected from otherwise present stitching vias remains a separate gap.
Existing documentation describes geometric containment, not verified zone
connection, so this is a missing requirement for the new contract rather than a
claim that the existing composed checker accepts the via-free board. The task's
connected-In1 requirement is not
an existing invariant that can simply be preserved: it needs implementation.

## Finite repair, using this checker

Keep this work inside `rf_geometry.py`, its fixture/tests, and `docs/rf-geometry.md`.
Do not introduce a second RF checker or an independent obligation framework.

1. **Exact declaration and width comparison (bounded first change).** Permit zero
   only for CPW tolerances through a nonnegative validator, keeping null as UNKNOWN
   and rejecting negative/nonfinite/bool. Compare zero-tolerance widths as exact
   native integer nanometers against a decimal-derived emitted target. A target
   that is not representable on the native grid must refuse or be UNKNOWN; never
   silently round an arbitrary source value into acceptance. No added epsilon is
   needed for integer widths. Positive-tolerance callers retain existing semantics.
   Keep exact gaps UNKNOWN until the following evidence is available.
2. **Native identity, provenance and connected reference.** Bind the native report
   to the full probe-request identity and board hash, including nets and layers.
   Extend existing extraction to retain the source UUID and primitive/edge
   provenance needed by exact evaluation. Retain explicit unknown provenance for
   filled or clipped boundaries that cannot be attributed. Obtain native connected
   copper components including filled zones and plated layer transitions, anchored
   to a physical ground terminal; require every reference region used under the RF
   strip to belong to such a component. A shared net code is not connectivity.
   Reuse the installed native connectivity facilities; validate the actual KiCad
   API against this fixture before choosing its binding. If the required component
   evidence cannot be produced, report UNKNOWN, not coverage PASS.
3. **Exact gap certificates and coverage.** For parallel integer straight edges,
   compare squared distances with integer/rational arithmetic, including track
   half-width, and split longitudinal intervals at primitive/projection changes.
   This can prove or disprove equality on whole intervals without sampling holes.
   For diagonal serialized constructions and curves, retain the native generation
   and quantization provenance. A serialization allowance must come from a verified
   coordinate-rounding operation and its directed error propagation, not from an
   observed delta. Prefer exact comparison to a canonical emitted construction
   generated at the nominal dimensions when that construction is known. A mere
   uncertainty interval containing the target proves compatibility, not equality;
   leave it UNKNOWN when no equality certificate exists. Approximation bounds can
   prove DEFECT when the target lies outside the certified interval, but cannot
   turn overlap into PASS. Curves without a certified construction remain UNKNOWN.
   Disclose per-interval error terms, provenance, side, source UUIDs, covered length,
   and ungraded transition intervals. No declared exception means ungraded
   transitions prevent whole-section exact PASS. Preserve independently proven
   defects even where other intervals are unknown.
4. **Native emission and consumer closure.** First demonstrate one ordinary
   straight-section native positive with actual .2032 mm boundaries (e.g. explicit
   legal coplanar conductor primitives rather than assuming a zone-clearance
   setting emits equality). Preserve .2032 mm required clearance. Verify whether
   the production emitter can retain those boundaries through refill. If not,
   return that emitter limitation to 449; do not change the gap policy inside the
   checker. The canonical writer then binds the source declaration to the emitted
   .293 nominal, keeps the inverse record separate, and recompiles the routing-stage
   requirement map. An unrouted candidate need not pass this post-route obligation.

Suggested bounded allocation, **estimates, not promises**: one 30-minute slice for
(1) plus located coverage/identity refusal; one 60-minute slice for native
connectivity with a saved floating-zone negative; one 90-minute slice for straight
interval certificates and three-orientation native controls. Curved acceptance
remains explicitly UNKNOWN unless its certificate fits that slice. Stop at each
slice with retained evidence; no extension or new broad campaign is implied.

## Acceptance controls for the repair

- Exact .293 width passes; +/-1 native nm fails. Fractional-nm target, NaN, bool,
  negative tolerance and stale source refuse; null remains UNKNOWN.
- Positive tolerance behavior remains covered by the existing tests.
- Each flank separately: exact saved boundary positive, narrower and wider
  ordinary sections negative, missing one flank negative, a widening strictly
  between old sample stations negative. Test straight, diagonal and arc cases;
  unsupported provenance must give UNKNOWN, never PASS.
- Missing In1, floating tagged In1 fill, wrong net, wrong layer, mismatched probe
  request, and stale board/source negative controls through the real check path.
- Transitions are located and reported as ungraded, not new launch exceptions.
- Native save/reload and refill preserve the tested geometry; retain their boards,
  exact declarations, request/native/result files and native connectivity/DRC data.
- Independent review checks uncertainty semantics as well as fixture results.

## Validation actually run

Both commands ran on proximal with `native-admit --class test --weight 2` while
another weight-2 job occupied the remaining capacity. No broad suite, new image
build, solver request, source bypass, or fabricated tolerance was used.

```sh
native-admit run --class test --weight 2 --max-wait 120 --label agent510-492-native -- \
  docker run --rm --network none --security-opt apparmor=unconfined --user 1000:1000 \
  -e PYTHONPATH=src -v "$PWD:/opt/agent-ee" -w /opt/agent-ee \
  agent-ee:exec-149276250b0861ec /usr/bin/python3 docs/agentee-492/probe.py build/agentee-492

native-admit run --class test --weight 2 --max-wait 90 --label agent510-492-affected -- \
  docker run --rm --network none --security-opt apparmor=unconfined \
  --security-opt seccomp=unconfined --security-opt systempaths=unconfined --user 1000:1000 \
  -e PYTHONPATH=src:tests -e AGENT_EE_REQUIRE_TOOLCHAIN=1 \
  -v "$PWD:/opt/agent-ee" -w /opt/agent-ee agent-ee:exec-149276250b0861ec \
  /usr/bin/python3 -m unittest test_rf_geometry.DeclarationTests test_rf_geometry.NativeEvaluationTests
```

Diagnostic: exit 0, nine native boards retained. Existing affected tests: **28/28
PASS, 3.611 s**, no skips. These establish current behavior, not repair completion.
Native DRC was not run in this diagnostic; no DRC-clean claim is made. Single-flank,
full real-check stale-report, and certified exact positive controls remain in the
repair acceptance list; they were not silently counted as completed.

Evidence is retained at
`/home/halbritt/.local/share/agent-ee/reviews/2026-10-23-agentee-492-agent510/`, with
`SHA256SUMS`, raw native boards/reports, result.json, command logs, and runtime/source
identities. The source stays at the frozen revision; no rebase onto concurrent
479/489 changes was needed for this diagnostic-only branch.

Doctrine packet `pkt-b43021488d1964bc`, validated release corpus
`corpus-2026-07-12-a11702cc9217`, informed the separation of evidence from repair
(`universal-evidence-before-intervention`) and retention of the authorized no-change
fallback (`universal-repository-contract-precedence`). It supplies no RF physics or
numeric allowance. Primary evidence here is the native reproduction and source.

The optional repository-wide documentation checker was queued with weight 2 and
returned admission timeout (exit 75) after 60 seconds; it did **not** run. No retry
or admission bypass was used. `git diff --check` is clean. The diagnostic source
and this report are the only committed changes; production checker behavior is
unchanged, so the implementation acceptance gate remains HOLD.

## Follow-up emitter feasibility

The subsequent bounded native emitter experiment is recorded in
[emitter.md](emitter.md). It demonstrates exact straight-section physical gaps
without changing the required clearance, and does not implement the pending
checker repair. Its runs use admitted `ae-ci-exec` on peecee-wsl. Fable's earlier
G2 process correction is accepted: the original direct Docker invocation lacked
the wrapper's lease label and resource flags; it must not be reused as the run
procedure. The reviewer independently reproduced the original results through
`ae-ci-exec` and completed the previously blocked documentation check.
