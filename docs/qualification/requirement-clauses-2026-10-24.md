# Compound requirements in the normal build, 2026-10-24

The requirement compiler now accounts for individual clauses of a compound
specification item without changing the specification. Each clause retains
its parent ID, exact UTF-8 byte span and source hash. Unresolved clauses still
block manufacturing. This supports integrating a check that covers part of
an item without misrepresenting the whole item as verified.

Source implementation: `e68d20514dd5c02eef2e410f793e0c0f132d20b3`.
The change extends the existing prose-span validator; it introduces no new
check kind or second compiler. Requirement clauses cannot become context,
omit non-whitespace bytes, overlap, alias source IDs or nest. Derived envelope
obligations remain atomic. Physical-only parent requirements keep that
restriction in every child. Existing unpartitioned scope, baseline and coverage
identities were compared with the prior implementation and remained equal.

## Actual acquisition workflow

The previous bottom-plane acquisition input was copied intact. Its specification,
parts, circuit, placement, plane plan and numerical constraints are unchanged.
Only the requirement map changed, and `adc-ties.json` was added:

| Original requirement | Newly connected coverage | Still unresolved |
| --- | --- | --- |
| `ACQ-ELEC-6` | `ACQ-ELEC-6-TIES`: five independent native rules for grounded ADDR/AIN2/AIN3/GND and explicitly unused ALERT | Host/analog power sequencing and prohibition on driving J2 pin 4 |
| `ACQ-MECH-3` | `ACQ-MECH-3-DISTANCES`: eight final native supply/return/filter pad-distance checks | Routed loop lengths and return-path quality |

The pin oracle reuses rules exercised in the earlier independent electrical
diagnostic. It is now part of the normal build's generated scope and binds to
the exact grounded/unused-pin clause. Generator parity remains a separate check.
The distance oracle already existed; its coverage now binds to the exact
distance sentences instead of leaving the entire compound item unresolved.

The fresh ordinary build completed requirements, parts, scaffold, schematic,
placement/routing and artwork. Placement/routing took 318.231 s within the
900 s budget: 105 segments, six vias, 175.1355 mm copper length, zero opens,
zero native error-severity findings and zero via centers in SMD pads. No
optimization or superiority claim follows from these measurements.

The final independent diagnostic review executed all **21 declared checks**:
20 `ok/pass` and the native netlist prerequisite `ok/info`. The five pin rules
have no violations or unknowns. All eight required distances pass the unchanged
3 mm limit, ranging from 1.810085 to 2.956455 mm. Native distance checks do not
prove physical body clearance or a good routed return path.

- Final native PCB SHA-256:
  `b1cf2a2564dd3f5a5c429cc5c775857905a36a6b7a2f12d21a3ddf8df380b353`.
- Review candidate:
  `ab9c26671ccbf36c61e9081a2666f28ed3719fb59b6994d6e47a5fcf2ade389d`.
- Review exit 1 is expected `incomplete`; independent `verify` exits 0.
- Diagnostic fabrication outputs: nine plots, two drill files, 12 BOM rows,
  17 CPL rows, 17 hash-bound files and eight sealed artifacts, artifact ID
  `0207f53552428550ac8f4c4a10bf642ceda3358ac858fee47cbb934a30fefc63`.
  Supplier rotation convention remains `not_declared`.

The default package resume exits 1 and names all eleven unfinished original
items, using `ACQ-ELEC-6-REMAINING` and `ACQ-MECH-3-REMAINING` for their remaining
clauses. No native project or manufacturing package is accepted. Physical
validation and supplier preview have not occurred.

## Negative and regression evidence

A separate input changes only the circuit declaration: U2 pin 1 moves from
GND to V3V3. The normal producers regenerate the native schematic; no native
file is hand-edited. ERC and generator parity pass. The independent pin check
reports `U2-1-6` as a violation, with four other pin rules satisfied and no
unknown rule results. Independent evidence verification exits 0. This negative
stops after schematic production: absent planes and incomplete routing also
prevent its unrelated board/manufacturing checks from passing.

The strict supported environment ran **145 tests in six modules, no skips**:
requirement maps, envelope mapping, requirement scope, spec capture, topology
rules and electrical locality. The retained red test shows the old compiler
refusing a complete clause partition. Negative tests cover missing/overlapping/
out-of-source/empty spans, boolean offsets, identifier aliasing, context
promotion, physical-only bypass and envelope-field reclassification.
Documentation and origins checks pass.

Environment: `agent-ee:exec-b5ff7ad833e5ea01`, image ID
`sha256:157e2c6fd1777245a570b7251dd6bbc6504cf12dc9091a5f4c127ef0c8742325`,
KiCad 10.0.7 and kicad-route
`80e144437b48a3de77b226d0e9465c16b9ab8b7e`.

```sh
ae-ci-exec . -- env PYTHONPATH=src:tests \
  python3 scripts/strict_suite.py --jobs 2 \
  test_requirement_map test_requirement_map_envelope test_requirement_scope \
  test_spec_record test_topology_rules test_electrical_locality

ae-ci-exec . -- bin/agent-ee build run \
  build/requirement-clauses/acquisition-build.json \
  --out build/requirement-clauses/acquisition-build-native --through artwork

ae-ci-exec . -- env PYTHONPATH=src:tests \
  python3 build/requirement-clauses/review-build.py acquisition

# Expected refusal: unresolved sibling clauses cannot become manufacturing passes.
ae-ci-exec . -- bin/agent-ee build run \
  build/requirement-clauses/acquisition-build.json \
  --out build/requirement-clauses/acquisition-build-native --resume
```

The diagnostic review driver only points the generated scope at the actual
completed native candidate. A supported build command should perform that
selection directly; this remains an integration gap, not a finished normal
intent-to-manufacturing workflow. The I2C timing/configuration, power and
capacitor applicability, routed return paths, mechanical fit and clean full
reproduction obligations remain. No additional KRT parity is established here.

The engineering decision and its alternatives are retained with doctrine packet
`pkt-f5d8711d7c6f02c9`, hash
`f5d8711d7c6f02c9050b66b294370a4a4204e648cb968ba450a31eaadc795d13`.
Its nine remaining expert-domain-model obligations are explicitly nonmaterial
to reusing this existing byte-partition contract; they do not qualify electrical
or mechanical judgments.

Immutable evidence:
`~/.cache/agent-ee-qualification/agent545/requirement-clauses-20261024/`.
Its 7,304 regular files and one internal symbolic link were verified before
making the bundle read-only. `SHA256SUMS` SHA-256 is
`5ec730b1272a59c4e47532bee2e364b910a767b5484a2d2b91232538d9ec2b83`;
`SYMLINKS.json` SHA-256 is
`5afc785e6be85045adec8918d32607e8ac195452e3bd733fe318fdb010b56093`.
The complete working evidence is retained under `build/requirement-clauses`;
the immutable copy is under the bundle's `workspace/` directory. Input-selection,
negative mutation, source preparation, review driver, tool logs, native projects
and diagnostic exports are retained there. This seal records evidence, not
manufacturing acceptance.
