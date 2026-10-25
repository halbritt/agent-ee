# Nominal differential impedance through placement and routing

Agent-545, 2026-10-25. **Qualified capability milestone.**
This targeted software article does not make a circuit or manufacturing-release
claim. The accepted 17-part acquisition package is unchanged.

## Behavior and engineering boundary

The opt-in `router.pair_impedance` request carries a target, tolerance, named
reference net and total terminal-escape length allowance per member. Exactly
one outer `pair_layers` layer is required for this milestone. The router reads
the explicit native stackup, solves a nominal differential width with its
existing microstrip model, respects the native minimum width, and passes the
solved geometry to ordinary differential routing. It preserves authored rules.
The placement job runs the saved-geometry check again after its final zone fill.

The original owned `--impedance` path solved the trunk width but retained old
terminal/offset geometry: the resulting 0.2745 mm gap violated the native
0.34 mm minimum. Consistent geometry selection repairs that failure without
editing the board after routing. Existing default routing remains unchanged.

Native load/save also inserts a default relative permittivity of 4.5 when the
original stackup omits it. A retained normalization probe reproduces this.
The normal placement job checks the original, hash-bound stackup before any
materialization, so generated defaults cannot discharge a missing-data obligation.

The checker reloads the final native board, measures actual parallel widths and
gaps, calculates nominal impedance, and accounts for every pair track. Actual
filled reference polygons must cover the traces. Uncovered model length must
be connected to a terminal pad and remain within the explicit escape allowance;
a mid-trunk discontinuity cannot consume that allowance. Missing coverage,
reference gaps, wrong widths or unattainable requests fail. Vias, arcs,
coplanar pours and inner-layer models remain unqualified in this option and
are refused. They remain obligations of the larger capability programme.

## Article and assumptions

Two fixed signal terminals and two through-hole ground anchors occupy a
20 by 20 mm four-layer board. All footprints have explicit block membership.
The signal is on F.Cu over an In1.Cu GND plane produced by the ordinary plane
stage. Explicit copper thickness is 0.035 mm, outer prepreg is 0.2 mm with
relative permittivity 4.2, and the 1.06 mm core makes the board 1.6 mm thick.
Native width remains at least 0.25 mm; nominal pair gap is 0.35 mm with native
0.34–0.36 mm limits. No product behavior is inferred from the `CAN+`/`CAN-`
net names.

The 100 ohm ±10% nominal target, 0.025 mm search grid and 3 mm total escape
allowance per member are agent-selected qualification assumptions. An initial
exploratory 0.5 mm allowance failed and is retained as a negative. It was not an
owner requirement; the explicit 3 mm correction accommodates terminal launches
while remaining below the inherited native 6 mm uncoupled limit. The original
0.5 mm requirement is not reported as passed. No controlled-impedance claim
applies to the escapes, pads, solder mask or manufacturing variation.

At 0.325537 mm width and 0.35 mm gap, the routing model calculates 99.7409 ohms.
An independent calculation using unchanged KiCad 10 coupled-microstrip source
at `4e7360bc6f23f34a0de71f72a81c6f57ebf60cc6` gives 102.1797 ohms at 1 MHz.
Eight exact source files, URLs, hashes, harness and executable are retained in
`build/impedance/kicad-calculator`. This is analytical agreement, not a field
measurement, supplier stackup approval or finished signal-integrity analysis.

## Native results and rejection

The public placement command completes in 36.941 seconds; the actual build
placement producer completes in 37.204 seconds. Both save six F.Cu segments,
zero vias, 19.6273 mm total copper, zero opens and zero native findings. Each
member has 7.3 mm of coupled trunk and 2.513635 mm of terminal escape. Saved
width is 0.325537 mm and gap is 0.35 mm. The two functional blocks, fixed
anchors and authored rules survive. The build producer exercise is diagnostic
because this article has no circuit schematic or electrical requirement map.

| Artifact | SHA-256 |
| --- | --- |
| Public final PCB | `05943d85b66293beded72115c166132c2b44c32c41b73e1f579403a40a5e5e44` |
| Build-producer final PCB | `aafe78ae249c550f92e965fad9e07537a6ad52d83356de85b20cc7dc5dcca1d0` |

Six strict public-workflow tests pass without skips. Missing original dielectric
data refuses before placement. A missing reference and the strict 0.5 mm escape
limit produce fully connected boards that remain ineligible. A changed target
refuses resumption without changing the prior checkpoint. These are failures
of the declared obligation even when connectivity is complete.

The router's focused module passes twelve native tests. They include the public
route and public revalidation of wrong width, missing/wrong reference, a plane
slot with complete connectivity, a mid-trunk neck, missing pair coverage and
the original strict escape limit. Requests with missing dielectric data,
unattainable targets and unsupported layer selections refuse before routing.
The full router smoke initially reports 1044 passes and three failures across
1047 cases, with zero skips. All three failures came from the scratch runtime's
nested links being absent inside the sandbox. Using the existing supported
whole-runtime-root link corrects the environment; those exact three cases pass
again. The original failed smoke is retained. Including the final focused
module's three additional cases, all 1050 distinct cases have passing evidence
in `build/impedance/router-qualification.json`. Router implementation is
integrated at `5801a9f39618853ab941beac7069c68a5270e16e`.

Artifact inspection caught an invalid earlier neck control: a borrowed KiCad
vector changed after track removal, accidentally disconnecting the board. The
corrected test retains scalar coordinates and asserts zero opens, escape lengths
below 3 mm, and only terminal-escape-connectivity defects from the model checker.
That native control passes with 2.613635 mm escape per member. Native DRC also
correctly flags its widened pair gap. The original result is retained and
superseded, not counted as proof of connected-neck rejection. Test correction
`501b26c2758022ffc0e000b02727d1ae09b38b00` changes no production file: all 312
`bin/` and `src/` files match the qualified runtime pin `5801a9f`. Agent-ee keeps
that immutable runtime pin; the later test-only revision is recorded separately.

## Stock comparison

Latest observed stock KRT is `4cf6d29ddbe7d19b511352d0abbb26842f609be6`,
dated 2026-10-24. Its fresh Python source uses the previously compiled stock
native extension only after byte-for-byte continuity of all fourteen tracked
files in the Rust sub-tree was verified. Exact archive, source and binary hashes are
retained. Stock passes this article's native checks and the final independent
impedance check, including its legitimate narrower terminal escapes. The first
stock invocation included an owned-only flag and failed usage; that result is
preserved and is not classified as a stock routing failure. No superiority is
claimed on this article.

The integrated owned replay and stock use the identical original PCB
`714e7afbe6b9e2d47cf076879719dd8fb227b98d8c05a81cb2919e27ffff91ce`,
unchanged project/rules, F.Cu, a 0.025 mm grid, the same strict-size/fabrication
and escalation controls, copper protection environment and 180-second backend
allowance. The owned replay calls production native pair detection and impedance
synthesis before the backend; stock solves inside its backend. The public job
and build producer above independently exercise the normal supported workflow.
Neither output contains manual geometry repairs.

| Backend | Opens / native findings | Nominal impedance (ohm) | Copper (mm) | Vias | Backend seconds |
| --- | --- | ---: | ---: | ---: | ---: |
| Owned `5801a9f` | 0 / 0 | 99.7409 | 19.627270 | 0 | 2.811 |
| Stock `4cf6d29` | 0 / 0 | 99.7410 | 19.627578 | 0 | 2.740 |

Stock retains 7.6 mm coupled trunk and 2.213789 mm terminal escape per member.
Its 0.25 mm terminal widths satisfy the unchanged native floor. Both outputs
pass the same independent final checker. Backend timings exclude the owned
preflight synthesis and establish no speed advantage. A single observation also
establishes no broad reliability advantage. The matched owned PCB is
`8004bd5438e28ef8287b1a40119ff8a58b9e62cb20e299143fd12be7d81c3518`;
stock is `5529fd6ff84297a8c91eb78899f573830fd4a59a6add212b0587ce933e1d0ebb`.

## Installation and reproduction

Fresh image `agent-ee:exec-bcedd7da327e9dc8`, ID
`sha256:a9e2d94afd6c87526d4bd42ddd051a2c7a4272c2d9b0cead4eff5dbefb7659b0`,
builds and verifies the pinned router offline; all 103 placement Rust tests pass.
Observed runtime is KiCad 10.0.7, Python 3.12.3, owned Rust router 0.23.1,
NumPy 2.5.4, SciPy 1.18.1, Shapely 2.2.0 and Pillow 13.0.0.
All 49 required agent-ee modules have passing evidence for their 983 distinct
tests, with zero skips. The initial 48-module pin run had one missing-input
failure: the external `esp_prog` bundle was not mounted for its capture check.
Restoring the exact four hash-verified input files and rerunning that case
closes it; the other 976 cases and all six new public tests pass. The initial
failed gate and an import-path-only retry are preserved, not described as passes.
`qualification-summary.json` identifies the correction and every required
module. Implementation is integrated with this record. No unchanged acquisition
build was repeated.

```sh
ae-ci-exec . -- bin/agent-ee place run \
  build/impedance/public-qualification/inputs/job.json \
  --out build/impedance-public-replay
ae-ci-exec . -- env \
  AGENT_EE_IMPEDANCE_EVIDENCE=/opt/agent-ee/build/impedance-requalification \
  python3 scripts/strict_suite.py --jobs 1 test_placement_job_impedance
```

Supplier stackup tolerances, solder-mask effects and physical impedance
measurement remain explicit later obligations for any manufactured design.
Stripline, multilayer impedance, coplanar models, propagation-time matching and
broader reference-path repair remain required subsequent capability work.
The overall engineering-intent-to-manufacturing goal remains unfinished.
