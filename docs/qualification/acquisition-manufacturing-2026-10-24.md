# Current acquisition manufacturing path, 2026-10-24

The ordinary build now carries the sourced manufacturing profile, final placement
checks and fabrication exports through the regenerated TPS7A20 acquisition
design. **The complete design remains unaccepted.** All 20 declared checks ran
successfully, but eleven original electrical, mechanical and reproducibility
requirements remain unresolved and correctly prevent the default package build.

Production source was unchanged at `ab1f7e935d53d14ea25c4c653b246a741197721d`.
This iteration integrates existing checks into the engineering input map; it
does not introduce a second manufacturing checker. KiCad was 10.0.7 in
`agent-ee:exec-b5ff7ad833e5ea01`; kicad-route was
`80e144437b48a3de77b226d0e9465c16b9ab8b7e`.

## Actual build and independent results

The plan is `build/agent545/acquisition-manufacturing-build.json`; its captured
source bundle is `acquisition-manufacturing-inputs`. The original specification,
parts, connectivity and placement constraints remain intact. The map adds the
previously missing geometry, envelope, placement, profile and package coverage.
The existing explicit `nominal-design` stackup mode checks the authored nominal
1.6 mm design and supplier tolerance without claiming unpublished physical core
thicknesses are qualified. No impedance acceptance follows from that mode.

The resumed run completed requirements, parts, scaffold, schematic,
placement/routing and artwork. Each stage consumed its predecessor's actual
output. Native files were not patched or substituted. Routing took 296.655 s
within the declared 900 s budget: 199 segments, ten vias, 216.6868 mm total copper,
zero opens and zero center-in-pad vias. Artwork then repaired visible fields
through the existing bounded producer. These measurements are observations,
not an optimization or KRT comparison.

Final artwork PCB SHA-256:
`8b441809e8ca8073f0aeb2bc90563452ccee60bbe227562a3e10b433a9873992`.
Independent review candidate:
`52254b7eae3f571a3648642408b0563e96616951b7fcad7d3b583e566960c398`.

| Required check in this diagnostic scope | Observed result |
| --- | --- |
| ERC, DRC with schematic parity | Pass; no unwaived violations |
| Native netlist and independent topology | Export succeeds; exact topology passes |
| Parts membership and assembly readiness | Pass; all 17 components and adopted passive-rating provenance checked |
| Outline, copper layers and nominal stackup | Exact saved-design checks pass |
| Fixed J1/J2/J3 anchors | All pass after routing and artwork |
| Copper widths, courtyards and outline containment | Pass |
| Eight hard pin-locality relationships | Pass on final native board |
| Declared SMD-via separation | Pass for the one declared DFM rule |
| Selected fabrication profile | All eight declared numeric supplier minima pass |
| Board-mounted model heights | Pass; excludes mated plug/access qualification |
| Fabrication and assembly package | Nine layer plots, two drill files, 12 BOM rows and 17 placements pass independent consistency and sealing |

The netlist exporter returns `ok/info`; it is a prerequisite, not an electrical
acceptance assertion. The other 19 checks return `ok/pass`. Evidence verification
and the checks-only gate both return zero. Checks-only review returns one because
independent review and complete design acceptance are unfinished.

The supplier limits are captured current JLCPCB facts with original pages and
hashes. The selected 0.45 mm all-hole spacing is a conservative engineering
selection from the supplier pad-hole minimum; it is not misrepresented as the
supplier's via-only minimum. Profile coverage does not claim every supplier DFM
rule is implemented. Native CPL orientation is checked; supplier preview and
supplier-specific rotation approval have not occurred.

## Refusals and retained limitations

Default execution refused unresolved coverage before placement. An explicit
`--through artwork` resume produced the diagnostic candidate. A subsequent
default resume again refused at package acceptance, naming all six
`ACQ-ELEC-*`, four `ACQ-MECH-*` and `ACQ-DELIV-2` requirements. No limitation was
converted to a pass to exercise the later stages.

A separate copy of the final review has its first CPL X coordinate shifted by
1 mm. `agent-ee verify` returns two with
`execution/review evidence changed; prior acceptance is stale`. The native board
and original positive package remain unchanged. This negative establishes stale
artifact rejection; it does not impersonate a freshly resealed semantic export
test. The earlier semantic consistency and stale-output regressions remain
separate evidence.

Remaining work includes applicable electrical operating/capacitor bounds,
complete routed loop and return-path constraints, mated connector height and
access, and a clean reproducible accepted full run. STEP model availability and
render success cannot close those obligations. No physical test, supplier
preview, order or upload was performed.

## Commands and retained inputs

These commands use the retained input bundle and supported native executor.
Use a new output directory for reproduction. They do not establish a complete
clean-install qualification recipe.

```sh
ae-ci-exec . -- bin/agent-ee build run \
  build/agent545/acquisition-manufacturing-build.json \
  --out build/agent545/acquisition-manufacturing-build-native --through requirements

ae-ci-exec . -- bin/agent-ee build run \
  build/agent545/acquisition-manufacturing-build.json \
  --out build/agent545/acquisition-manufacturing-build-native --resume --through artwork

ae-ci-exec . -- bin/agent-ee review \
  build/agent545/acquisition-manufacturing-build-final.scope.json --checks-only \
  --out build/agent545/acquisition-manufacturing-build-final-review --max-seconds 900

ae-ci-exec . -- bin/agent-ee verify \
  build/agent545/acquisition-manufacturing-build-final-review

# Expected refusal: authored electrical/mechanical/reproducibility coverage remains open.
ae-ci-exec . -- bin/agent-ee build run \
  build/agent545/acquisition-manufacturing-build.json \
  --out build/agent545/acquisition-manufacturing-build-native --resume
```

The diagnostic scope uses the scaffold's generated scope, rebound to the final
candidate with the same input selector used by the build; checks, requirements,
ratings provenance and limitations are preserved. When reproducing with a new
run, rebind that input selector to the new run's reported final candidate.

Before regeneration, the unchanged previous PCB `e802bb4a7737…` also passed the
16-check manufacturing subset and stale-CPL negative. Its power-removal model
passed both 1 mA external-load and external-open cases, with peak OUT-minus-IN
of -0.0703343 V and -0.0636755 V respectively. These nominal vendor-model results
omit temperature and loaded ground current, use nominal capacitors and an
explicit ADC power-only load. They do not establish component-corner safety.

The bounded alternative-regulator investigation did not change the circuit.
TPS73633 has useful accuracy specifications, but needs a changed minimum-load
and input-headroom design. Its retrieved TI SBVM439 model is encrypted PSpice
for the adjustable TPS73601, so it cannot qualify the fixed part through the
supported ngspice path. That model limitation is retained, not bypassed by a
fabricated replacement model.

The input-integration decision has a retained doctrine receipt
`pkt-59e8e817b2fd83c3`, content hash
`59e8e817b2fd83c3ab97e39b26987623c8a680151b543b45e3ee1056617954a9`.
Its conclusion is limited to the observed diagnostic integration and preserved
acceptance refusal; generic source-change obligations are explicitly nonmaterial
because this iteration changed no production implementation.

The read-only evidence bundle is
`~/.cache/agent-ee-qualification/agent545/acquisition-manufacturing-20261024`:
6,857 hashed files and one internal `latest` symbolic link, whose exact target
is recorded in `SYMLINKS.json`. All file hashes and the link target were verified.
SHA-256 of `SHA256SUMS`:
`a05dec9a96b7932e6949c2021c26b6616385f30f6e81a48770830fa899238fbd`.
It retains the inputs, six-stage run, individual checks, default refusals,
negative copies, original source captures, nominal power-removal evidence and
native renders bound to each exact board. The complete goal remains unfinished.
