# Coupled layer transitions through the supported workflow

Agent-545, 2026-10-24. **Qualified capability milestone.**
This targeted software article makes no circuit, impedance,
return-path or manufacturing claim. The accepted acquisition release is unchanged.

## Behavior and correction

The normal placement job and build placement producer now carry
`router.pair_layers`, for example `["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]`.
The router refuses duplicate, disabled and non-copper names and independently
checks the saved layers of newly routed pair tracks. Existing valid copper and
through-via spans retain their native semantics. Omission keeps the previous
outer-layer default. Layer selection is bound to job/resume identity.

Native routing-only rule areas survive placement. Areas that restrict pads,
footprints or placement regions, and existing copper pours, still refuse before
solving. This does not silently treat a placement obstacle as safe.

The original article exposed two interacting defects. Straight layer transitions
used the turn-fanout formula, arbitrarily selecting an inner pair member and
shifting the shared track centerline. Later cleanup changed the lengths that
the matcher had already measured. Straight transitions now retain symmetric
fanout using the existing clearance-derived via offset. Cleanup precedes
intra-pair and AC tuning; the current generated copper is synchronized again
after tuning. Turning geometry and authored native rules are unchanged.

The retained isolation runs distinguish the fixes: moving cleanup alone still
fails native skew; symmetric fanout alone misses the requested planar-copper
tolerance. Disabling cleanup, adding center joins and compensating with other
meanders did not provide an adequate repair. Those failed experiments remain
under `build/pair-transition`; no manual geometry repair enters the public path.

## Article and native results

Two fixed terminals on a 45 by 40 mm four-layer rectangle launch orthogonally:
J1 at (45,50) mm, J2 at (75,70) mm rotated 90 degrees. Both belong to the
`interface` block. A native F.Cu/B.Cu track/via barrier at x=58..62 mm spans
the board, forcing an inner-copper crossing. The barrier allows pads and
footprints. Native constraints remain 0.25 mm track width, 0.35 mm nominal gap,
0.20 mm clearance, 70 mm maximum length, 0.15 mm maximum native skew and 6 mm
maximum uncoupled length. These are explicit agent-selected qualification
assumptions, not owner requirements for a CAN product.

The search uses a 0.1 mm grid, a 0.1 mm planar matching tolerance and 2 mm
meander amplitude. Changing search resolution or available meander room does
not change the native requirements.

The public job completes in 27.821 seconds and the actual build placement
producer in 27.986 seconds. Both save 25 segments, four vias, 89.7158 mm total
planar copper on F.Cu/In1.Cu, zero via-in-pad sites, zero opens and zero native
violations/warnings. Anchors, native rule bytes and the routing keepout survive.
The build-stage exercise is explicitly diagnostic because this article has no
schematic or electrical requirement map.

| Artifact | SHA-256 |
| --- | --- |
| Public final PCB | `c2d6c2d26cee16368c6909b0173a953a90bdad132686539807a61cc07ace3f82` |
| Build-producer final PCB | `1dc9b72e8e05ea3940a2cabb32025c1341e4fc320d43c6174947103a4efc4a53` |
| Preserved project | `43b807354f129afab690a23b9fe44e93a3a4a17e1b40ddfb2e57d99056b676b9` |
| Preserved custom rules | `2e77929d0278ba561cb67adfe74ddc271baf6631344338d35c930925412daffd` |

The paired planar lengths are 44.858293 and 44.857507 mm, a 0.000786 mm
difference. This sum excludes via barrels and differs from KiCad's native
electrical-path length. Both the requested planar tolerance and the separate
native skew rule must pass. No propagation-time result is claimed.

## Matched KRT comparison

The integrated owned router `86f586c3ae262be12552652e963a3e11ba7b9287` and
stock KRT `d24a7602012b96c5cfa610ea05a171646ad601bd` use the identical retained
native input (PCB `27a1f99daebceb531f286fc2cbf72f64046233f343ff1b7693a5afe6a66fc05a`),
unchanged rules, the same four permitted layers, 0.1 mm grid, 0.1 mm requested
matching tolerance, 2 mm amplitude and 180-second backend allowance. Input
copper protection and automatic rule-edit prohibitions are the same. The
final owned replay is `owned-integrated`; the unchanged stock result is
`stock-coarse`. Exact commands, native outputs and source/binary provenance are
retained. Both use KiCad 10.0.7 and the same Python/numeric package versions.

| Backend | Opens | Native violations | Planar difference (mm) | Copper (mm) | Vias | Runtime (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Owned, integrated | 0 | 0 | 0.000786 | 89.7158 | 4 | 3.933 |
| Stock d24a760 | 0 | 1 skew | 0.049315 | 89.6678 | 4 | 3.951 |

Stock's native skew is about 0.3339 mm against the unchanged 0.15 mm limit,
despite meeting the planar tolerance. This establishes a bounded correctness
advantage on this article. A single observation establishes neither a runtime
advantage nor broad reliability. D02 paired vias and portions of R02/R04 in the
[KRT source map](../research/krt-2026-10-24/krt-latest-capability-map.md) have
executable evidence here; it does not close their entire capability rows.

## Rejection, installation and reproduction

Five native qualification tests pass without skips. The public command exercises
the positive job, outer-only routing rejected with two opens and no routed
incumbent, and changed layer selection refused on resume with the prior
checkpoint unchanged. The other tests call the actual build placement producer
and native capture's pad/footprint/placement-area refusal guards. Router controls also
reject duplicate/disabled/non-copper layers and independently reject saved
inner tracks when revalidated against an outer-only policy. Analytical checks
exercise straight fanout symmetry and clearance at eight headings and both
polarities. No failed or skipped check is counted as passing evidence.

The router's full smoke initially reports 1029 passes, five failures and four
skips across 1038 cases, plus six passing subtests. Two old synthetic stub-swap
controls had implicitly relied on a 0.5 mm via after the production default
became 0.6 mm. They fail identically on unchanged main; explicitly declaring
their original 0.5 mm fixture geometry restores the controls and their negative
checks without changing production defaults or native rules. The other
failures/skips came from the scratch installation's absent manifest and an
omitted wheel-runtime setting. Focused native reruns close every affected case;
`router-qualification.json` accounts for all original cases. The original failed
smoke and intermediate failed retry remain intact.

Image `agent-ee:exec-645563099ea70ea4`, ID
`sha256:d1d55b4efdfc5cf1e88d7804ac40a02e8b01e3437a9e690b26393df7401d9a93`,
builds from the pinned router, verifies its installation offline and passes all
103 placement Rust tests. Runtime: KiCad 10.0.7, Python 3.12.3, owned Rust router
0.23.1, NumPy 2.5.4, SciPy 1.18.1, Shapely 2.2.0 and Pillow 13.0.0.

```sh
ae-ci-exec . -- bin/agent-ee place run \
  build/pair-transition/public-qualification/inputs/job.json \
  --out build/pair-transition-public-replay
ae-ci-exec . -- env \
  AGENT_EE_TRANSITION_EVIDENCE=/opt/agent-ee/build/pair-transition-requalification \
  python3 scripts/strict_suite.py --jobs 1 test_placement_job_transition
```

The 47 remaining pin-consumer modules pass all 972 tests without skips. Together
with the five new qualification tests, all 48 required modules and 977 tests
have passing evidence. Facts/pour producers and mirrored consumer behavior are
unchanged, as recorded in `consumer-audit.json`; the new CLI grade is confined
to explicitly selected pair layers. Implementation is integrated with this
record. The module roster, per-check outcomes, captured inputs and native
results are retained under `build/pair-transition`. Hybrid terminal fallback,
AC/inter-pair/time matching, controlled impedance, return paths, bus routing,
fanout, existing pours and placement-restricting rule areas remain open. This
milestone does not close the overall engineering-intent-to-manufacturing goal.
