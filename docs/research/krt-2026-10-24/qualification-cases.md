# Bounded qualification and matched-comparison proposal

This is an **unexecuted exercise specification**, not retained pass evidence. It translates the 69-row source map into a bounded suite for root's integrated workflow. Root may replace a proposed fixture only with one exercising the same requirements. No live telemetry-pod run is proposed. Existing esp_prog evidence remains the starting regression; it must be linked to the exact current implementation/native artifact before reuse.

## Suite and coverage

| Case | Native article and engineering purpose | Required variants and coverage |
|---|---|---|
| **Q1 esp_prog** | Retain the authorized existing regression, including its native project, supplied design intent, constraints and existing evidence. KRT has `kicad_files/esp_prog.kicad_pcb`; use root's complete native project rather than silently substituting a bare board without rules. | Functional groups, fixed interface edges, USB pair, ordinary routes/plane completion, final native placement and DRC. Reproduce clean setup and interruption/resume here. All four edge/corner/body-vs-courtyard regressions belong in Q6 rather than modifying the accepted Q1 input. |
| **Q2 fresh interacting-block design** | Generate a fresh low-voltage MCU/interface/sensor board through the normal agent-ee entrypoint from a concrete engineering-intent file. Suggested blocks: protected external connector, power regulation, MCU/clock/decoupling, sensor/analog filter, USB/data interface. Use suitable current documented parts and a selected available manufacturing profile. Root owns domain and exact requirements. | Schematic and circuit outputs feed PCB, placement and routing automatically. Native editable libraries/dependencies, current/load/voltage/filter calculations, pin mapping, multi-block grouping, allowed side choices, pin-locality, DNP/BOM/CPL, final fabrication package. No hand-authored downstream substitutes. Add a consequential electrical negative (e.g. wrong divider/voltage interface) that remains ERC/DRC-clean but fails functional acceptance. |
| **Q3 dense escape** | Two focused native articles already present upstream: `kicad_files/glasgow_revC.kicad_pcb` for BGA and `kicad_files/qfn_csi_underpad_diff.kicad_pcb` for QFN. Supplement with a small generated dense-array case if the BGA article does not exercise required dogbone/in-pad constraints within budget. | BGA channel/underpad/dogbone, QFN surface/underpad, front/back/rotation geometry, paired escapes, plane balls, cap repair. Preserve original input in each variant. Test forbid-via-in-pad and unavailable-gap failures, existing locked copper, required netclasses, final cap distance/side. Only the selected fanout methods necessary for coverage require full routes; frame-transform variants can be targeted native geometry checks. |
| **Q4 signal integrity and recovery** | A small native four-layer qualification design with explicit stackup, an 8-lane single-ended bus, coupled pair, series AC coupling and deliberate topology/clearance constraints. It is a targeted fixture, not an accepted engineering-intent example. | Multilayer/layer cost, MPS and bus strategy, forced rip victim, locked geometry, pair hybrid and multipoint terminal chain, impedance, length/time/intra-pair matching. Include missing dielectric, missing required match-group member, insufficient meander room, no-allowed-polarity-swap and pair-uncoupling negatives. Experimental AWX path is an optional separately labelled comparison, never substituted for normal route.py functionality. |
| **Q5 power/plane topology** | Small native design with wide rail/fine-pitch endpoint, two ground domains joined at declared tie, inner plane split/island, exposed thermal pad, and pre-authored Kelvin branch where that specific preservation feature is under test. | Exact plane refill, pad and secondary-layer island repair, stitching/fence, returns, neck length, via/paste policy, rail electrical bounds, sense topology. Negative: same-net tap bypasses Kelvin star or wrong-domain return remains DRC-clean but fails functional topology. Negative: late route cuts required pour. Declare model-only/native-unavailable outcomes incomplete. |
| **Q6 mechanics and intent** | Small native mechanical fixtures generated for testing: all four edge connectors; asymmetric fab/silk body and courtyard; corner contact; legal/illegal overhang; fixed holes/hardware; curved/concave outline with cutout; allowed back component; two repeated channels/array. | Final saved coordinates drive membership/side/pin-edge/access checks. Clean-DRC wrong-edge and violated decap proximity must fail. Test locked/declared poses, mating-face ambiguity, arrays/replication, keepouts, missing body geometry and explicitly recorded fallbacks. Height/enclosure obligations need agent-ee checks, since KRT's placement model is primarily 2D. |

Every case is finite. Initial engineering choice: one complete Q1 and Q2 flow; the two Q3 articles; one Q4 and one Q5; small Q6 variants. Start with one fixed seed per feature case, and the same predeclared placement multistart budget where placement selection is compared. Expand only in response to a measured defect, retaining the failed attempt. This bounds qualification without deleting required capabilities from scope.

## Paired protocol

1. Freeze **inputs**, not dependencies indefinitely: exact `.kicad_pcb`, `.kicad_pro`, `.kicad_dru`, native schematic tree, referenced libraries, intent, group map and required-check roster. Hash all of them. Both tools consume copies of the same input. Record intentional version migrations as separate prepared inputs and test preservation.
2. Record stock KRT main at execution time and its source/build hash. This research observed `222bb144744c3ddd979674e528bf8647e9ced8d6`, version `0.23.7`. Keep that observation reproducible; recheck upstream before claiming a later run compares latest. Record agent-ee and owned kicad-route revisions independently; they use a fork with selective adoptions, not stock latest.
3. Use the same KiCad version, native libraries, machine/container CPU/memory limits, thread/job policy, input state, layer policy, manufacturer constraints, permitted pin swaps, allowed footprint moves and seed list. Record resolved defaults and all `KICAD_*` knobs, not merely requested flags. A flag accepted by an adapter is not evidence it reached the engine.
4. Predeclare finite wall-clock budgets per stage and total case. Include setup/preflight, every seed, failed strategy, recovery, native checks and export. Report router-only and whole-workflow time separately. A time cap is an incomplete result, not a successful slower run.
5. Compare under identical authored rules. For stock paths that accept them, `--no-fix-drc-settings --escalation off --strict-sizes` are candidate controls; audit the exact result. Cap-repair and experimental paths have different semantics and may need explicit wrapper safeguards or a failed comparison finding. Do not weaken native rules or remove existing geometry to equalize success rates. An additional stock-default experiment must be labelled a different policy.
6. Complete correctness and coverage first. Required functional electrical, placement, physical, routing and manufacturing predicates are conjunctive. `DONE`, zero process exit, low score, zero custom DRC or improvement over baseline cannot substitute. Required check missing/stale/skipped/ungraded/unsupported means no acceptance.
7. Among accepted candidates compare final native copper length, through-via counts, layer usage, placement distances/violations, runtime and repeat reliability. Use three exact-input/seed repetitions for any deterministic/reliability claim. Report manual interventions as their own count and description; do not silently incorporate them into a normal-workflow pass.
8. Bind reports, renders and manufacturing files to exact final native hashes after all cleanup, cap moves, zone fill and file migrations. Keep both raw router output and final accepted output hashes. Validate package manifests and every required export.

## Source-checked command shapes

These patterns have not been executed in this research. `KRT`, `PY`, `CASE`, `WORK`, and `KICAD_CLI` below are **operator-supplied paths**; the input/intent/groups/chain/spec files must be generated or authored as case inputs first. Each routing output must retain project/rule siblings. Ordinary agent-ee commands must be filled by root from the current documented entrypoint, not invented here.

Setup for stock source in a separate writable build checkout, leaving the shared reference clone read-only:

```sh
"$PY" "$KRT/build_router.py" --from-source
"$PY" "$KRT/py_tools/validate_pcb_data.py" "$CASE/input.kicad_pcb"
```

Place and inspect, then select from actual fast-route evidence:

```sh
"$PY" "$KRT/py_tools/check_groups.py" "$CASE/input.kicad_pcb" --groups "$CASE/groups.json" --intent "$CASE/intent.json" --json "$WORK/groups.json"
"$PY" "$KRT/py_placer/place_simple.py" "$CASE/input.kicad_pcb" "$WORK/placed.kicad_pcb" --groups "$CASE/groups.json" --intent "$CASE/intent.json" --seeds 6 --clustered-seeds 6 --judge 6 --jobs 1
"$PY" "$KRT/py_placer/judge_seeds.py" "$WORK/judge.json" --chain "$CASE/judge-chain.txt" --intent "$CASE/intent.json" --jobs 1
```

`judge.json` is emitted in the placement run directory selected by place_simple; resolve that actual emitted path from its output rather than assuming the `$WORK/judge.json` spelling above. `judge-chain.txt` is an explicit design-specific sequence with `{in}`, `{out}`, `{dir}`, `{seed}` and `{run}` placeholders. It is shlex-tokenized, not a shell. After selection, full-route **the chosen placement board**, not the quick-routed board.

Example two-layer pour/route shapes; exact required widths, clearances, net scope and via settings come from the case, not these placeholders:

```sh
"$PY" "$KRT/py_router/route_planes.py" "$WORK/placed.kicad_pcb" "$WORK/poured.kicad_pcb" --nets GND --plane-layers B.Cu --no-fix-drc-settings --escalation off --strict-sizes
"$PY" "$KRT/py_router/route.py" "$WORK/poured.kicad_pcb" "$WORK/routed.kicad_pcb" --nets '*' --layers F.Cu B.Cu --no-fix-drc-settings --escalation off --strict-sizes --json-out "$WORK/route-summary.json"
"$PY" "$KRT/py_tools/fill_for_delivery.py" "$WORK/routed.kicad_pcb" -o "$WORK/final.kicad_pcb"
```

For four-layer/pair/fanout exercises, use every actual physical copper layer and the matrix row's feature flags. Preserve original rules and metadata through each command; absence of a record is a failure when later stages need it. Do not apply the two-layer example unchanged to a multilayer case.

Final inspection shapes:

```sh
"$PY" "$KRT/py_tools/check_floorplan.py" "$WORK/final.kicad_pcb" --intent "$CASE/intent.json" --json "$WORK/floorplan.json"
"$PY" "$KRT/py_tools/check_assembly.py" "$WORK/final.kicad_pcb" --intent "$CASE/intent.json" --baseline "$CASE/input.kicad_pcb" --json "$WORK/assembly.json"
"$PY" "$KRT/py_tools/kicad_unconnected.py" "$WORK/final.kicad_pcb" --json "$WORK/native-connectivity.json"
"$KICAD_CLI" pcb drc --refill-zones --format json -o "$WORK/native-drc.json" "$WORK/final.kicad_pcb"
"$PY" "$KRT/py_tools/render_placement.py" "$WORK/final.kicad_pcb" --groups "$CASE/groups.json" --intent "$CASE/intent.json" --review-sheet "$WORK/placement-review.png" --json-out "$WORK/placement-view.json"
```

The full native DRC report must be checked separately from the convenience unconnected count. Run native ERC and schematic/PCB connectivity parity on the case's final schematic project as well. All optional feature tools require corresponding requested-check coverage. This specification intentionally leaves manufacturing export commands to agent-ee's implementation owner, because inventing an unverified entrypoint would conceal missing integration.

## Mandatory negative outcomes

| Negative | Required rejection, independent of process/router flags |
|---|---|
| Remove a consequential final track or leave a multipoint rail pad disconnected | Native unconnected/component result fails required connectivity; no package accepted. |
| Change a functional electrical value or same-net Kelvin topology while preserving ERC/DRC cleanliness | Electrical requirement or topology check fails with part/pin/net context. |
| Move a connector to wrong edge or cap beyond declared pin distance while retaining clean DRC | Final saved-board placement check fails; no scalar score waiver. |
| Regenerate PCB after exports, alter one drill/BOM/CPL row, or reuse report from another board hash | Stale/inconsistent package rejected and exact changed member identified. |
| Skip a required impedance/length/floorplan check or supply an unavailable tool | Required coverage fails, even if upstream completion tool says DONE with UNGRADED. |
| Router narrows geometry and lowers output project minima | Candidate remains graded against original rules; unauthorized rule drift rejects. |
| Stop or time out mid-stage | No acceptance; complete durable checkpoint or explicit nonresumable state, then validated resume proof. |

## Evidence bundle per run

Retain immutable source inputs and their hashes; actual requirements/assumptions/owner decisions; all stage plans and resolved configuration; exact source/native/Rust/Python dependency versions; seed and CPU/time budgets; commands and bounded logs; each stage's native project plus library/project/rule siblings; raw and final geometry hashes; individual check coverage and reports; annotated top/bottom views; block membership/side/distance report; final package manifest and export validations; interruption/resume state; actual elapsed time and manual intervention count. Physical bench validation and supplier-preview work remain explicit separate obligations and must not be claimed without evidence.
