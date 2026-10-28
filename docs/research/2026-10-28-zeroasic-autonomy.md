# Zero ASIC ideas for autonomous board generation

Research date: 2026-10-28. Agent-ee source inspected:
`09bd4e74bdb5383d4de1da3cfb329783d43dc30a`.
[Owner request](2026-10-28-zeroasic-prompt.md) ·
[Concrete work items](2026-10-28-zeroasic-work-items.md) ·
[Implementation campaign](2026-10-28-zeroasic-campaign-plan.md)

The strongest opportunity is to automate engineering preparation for a bounded
circuit family, feeding agent-ee's existing build and qualification machinery.
Zero ASIC provides useful examples of structured design compilation, competing
implementation strategies, reusable block interfaces and deliberate fault tests.
Its public material does not supply a requirements-to-PCB generator to adopt.

Start with the existing acquisition design and one changed interface or load
requirement. Generate its captured inputs from the brief, carry the electrical
and physical obligations through the build, and retain independent evidence for
the resulting files. This extends the existing
[M2 recovery plan](../planning/2026-10-25-recovery/recovery-plan.md#m2--engineering-brief-to-captured-inputs)
and AGENTEE-525. Better routing is useful, but does not choose the circuit or
establish that it meets the brief.

## What “finished” should mean

The immediate software target is an editable native KiCad project and verified
Gerber, drill, BOM and CPL outputs, with every required file-release obligation
satisfied for that exact candidate. Functional requirements need electrical
evidence in addition to ERC, DRC and connectivity. Required unknowns remain
blockers, and the result lists limitations and outstanding supplier/physical
obligations.

Agent-ee already separates `checks`, `file_release`, `operational`,
`engineering_review`, `judgments`, `supplier` and `physical` in
[qualification.py](../../src/agent_ee/qualification.py).
Typed `accepted:true` is a software file-release claim; it does not establish
assembly or bench success. Preserve these distinctions in the autonomous
interface. See [build qualification](../build-workflow.md#qualification-and-current-claims).

The existing [qualification domain](../system-acceptance.md#supported-qualification-domain)
remains the product target. The first automated preparation case should use a
smaller, explicitly supported family. A request outside that family should
identify the unsupported requirement. The current telemetry-pod hold remains
in force; this research does not restart that campaign.

## Findings from the blog

These are descriptions of published material, not independently reproduced
Zero ASIC performance results. PCB applications below are our recommendations.

| Source | What is actually described | Application to agent-ee |
| --- | --- | --- |
| [PackageCompiler launch, June 9, 2026](https://www.zeroasic.com/blog/packagecompiler_launch) | YAML/JSON package specifications drive layout, DRC/LVS and KiCad/Gerber export. Logical modules/instances/nets and physical geometry share a design model. | Generate the existing captured input bundle from a validated engineering brief. Maintain explicit identity across circuit, native geometry and exported files. Package specification is a narrower input than arbitrary board requirements. |
| [DeepRoute tournament, October 8, 2026](https://www.zeroasic.com/blog/deeproute-tournament) | Complete routing trials vary net order, cost weights and layer assignments. Strategies branch, duplicate configurations collapse, and batches run in parallel. The score formula, tie breaking and finite stopping policy are unpublished. | Compare a small, replayable portfolio against the existing shortlist policy. Apply hard eligibility before optimization, bound total work, retain failed trials and include final grading time. |
| [Electrical analysis, October 27, 2026](https://www.zeroasic.com/blog/will-it-work-electrical-analysis) | Analysis reads routed geometry and materials, reports missing inputs and names its fidelity. Impedance, spacing, stitching and pair matching influence construction. Via resonance, return-path breaks, termination and DC drop still require designer action; electrical tournament scoring is future work. | Bind analysis to the saved board and expose model applicability. Close one measured electrical repair loop first. Package-specific dimensions and wavelength heuristics are not general PCB rules. |
| [EBRICK demo, March 19, 2024](https://www.zeroasic.com/blog/ebrick-demo) | A custom core fits declared UMI/GPIO interfaces, is exercised with Python-driven simulation, and enters a physical implementation flow. | One qualified block instance should supply both implementation inputs and verification setup. Independently check that its interface and operating conditions match the surrounding circuit. |
| [UCIe prototype, February 5, 2024](https://www.zeroasic.com/blog/ucie-open-source-design) | Digital and analog models are combined for channel/protocol testing, with omitted effects stated explicitly. | Consider mixed-model simulation only for a concrete unmet sequencing or interface requirement. Existing ngspice and interval checks remain useful for their qualified domains. |
| [OpenSTA integration, September 15, 2025](https://www.zeroasic.com/blog/opensta-release) | Timing analysis consumes implementation outputs and explicit constraints in a separate analysis stage. | Verify the final artifact against independent requirement evidence; generation and successful tool execution do not prove the required behavior. |
| [Wildebeest release, September 17, 2025](https://www.zeroasic.com/blog/wildebeest-launch) | Strategy selection depends on circuit characteristics, with benchmark comparisons across designs. | Select PCB experiments by density, package and constraint features; measure completion across frozen designs, including failures. No FPGA performance result establishes a PCB improvement. |
| [Tardigrade launch, July 9, 2026](https://www.zeroasic.com/blog/tardigrade_launch) | Machine-readable quality metrics support iterative tool use; the advertised engine is not supplied as a public implementation in the inspected organization. | Give the agent comparable measured outcomes and explicit reasons for incomplete work. Agent-ee already has structured results; concentrate on consumers that can act on them. |

The October 27 article limits the broader electrical-optimization language in
the October 8 article. Full autonomous electrical closure is not demonstrated
by these posts. Likewise, the PackageCompiler launch covers chip-package
implementation; it does not demonstrate board-level component selection,
circuit synthesis, procurement or physical validation.

The [PackageCompiler product page](https://www.zeroasic.com/packagecompiler)
offers beta access. No public PackageCompiler/DeepRoute implementation was
found in the eight inspected `zeroasiccorp` repositories or the linked product
articles. Open interfaces and output formats do not establish source-code
availability. The examples on the website are useful illustrations, not a
reproduced benchmark.

## Code worth reading

These links pin the inspected commits. They are source examples to learn from;
none was installed as an agent-ee dependency. No upstream EDA flow was run.

| Example | Specific behavior to inspect | Transfer and limits |
| --- | --- | --- |
| [SiliconCompiler branch/select flow](https://github.com/siliconcompiler/siliconcompiler/blob/059568587c1ffd8ace8442afbef59b01d7732348/docs/user_guide/examples/flowgraph_doe.py), [parallel execution example](https://github.com/siliconcompiler/siliconcompiler/blob/059568587c1ffd8ace8442afbef59b01d7732348/examples/parallel/parallel.py) | Indexed alternatives consume a common predecessor and join at selection; independent projects can run in separate processes. The example explains when parallel variants perform more work than a serial baseline. | A concrete execution pattern for bounded PCB trials. Keep agent-ee's existing orchestrator and host admission; equal wall time alone does not mean equal compute. |
| [SiliconCompiler `MinMaxBuiltinTask.select_input_nodes`](https://github.com/siliconcompiler/siliconcompiler/blob/059568587c1ffd8ace8442afbef59b01d7732348/siliconcompiler/tools/builtin/__init__.py#L109-L196), [selector tests](https://github.com/siliconcompiler/siliconcompiler/blob/059568587c1ffd8ace8442afbef59b01d7732348/tests/tools/test_builtin.py#L212-L272) | Reject error/goal-failing inputs before weighted scoring; missing required metrics raise; all-ineligible inputs yield no winner. Tests make the nominal best candidate fail. | Keep release constraints outside the score. Its absolute-value upper-bound goals and per-batch normalization are not suitable as generic electrical bounds or cross-run progress measures. |
| [UMI checker qualification](https://github.com/zeroasiccorp/umi/blob/6c7a519534e0ccd79db8a13643b1d3acc1132b5f/tests/sumi/test_checker_testbench.py#L1-L95) | Each checker sees valid traffic and an injected violation. Tests require the specific diagnostic; a fault can legitimately leave the process exit code at zero. | Qualify end-to-end acceptance gates with attributable board defects. Check the engineering verdict and evidence, not only the subprocess exit. |
| [Wildebeest BRAM preservation](https://github.com/zeroasiccorp/wildebeest/blob/676398124406ffc62a18dd0624471b950e18046f/src/synth_fpga.cc#L3155-L3265), [deliberately broken mapping](https://github.com/zeroasiccorp/wildebeest/blob/676398124406ffc62a18dd0624471b950e18046f/tests/unit/bram/bram-init-config-techmap-drops.ys#L1-L44) | Detect lost initialization after a transformation, with an error naming the failed boundary. A test intentionally drops that property. | Track semantic obligations through generated circuit, schematic, placement and package transformations. Existing hashes establish identity; obligation checks establish preservation. |
| [LogikBench parameterized mux](https://github.com/zeroasiccorp/logikbench/blob/58539b81d9c622a6b55a4cabde5d6b1288b9fe1e/logikbench/benchmarks/basic/mux/mux.py#L6-L37), [verdict extraction](https://github.com/zeroasiccorp/logikbench/blob/58539b81d9c622a6b55a4cabde5d6b1288b9fe1e/logikbench/common.py#L170-L222), [generation provenance](https://github.com/zeroasiccorp/logikbench/blob/58539b81d9c622a6b55a4cabde5d6b1288b9fe1e/docs/ai_json_schema.json) | Design variants, a self-checking testbench, distinct functional outcomes and source/model provenance. | Extend agent-ee's frozen generation benchmark with supported requirements variants and independent oracles. A provenance field saying “reviewed” cannot replace a bound review receipt. |
| [EBRICK common setup](https://github.com/zeroasiccorp/ebrick-demo/blob/054d7c4e0377e0412d38c46462d40c91cd5a4c80/ebrick_demo/ebrick.py#L15-L45), [testbench reuse](https://github.com/zeroasiccorp/ebrick-demo/blob/054d7c4e0377e0412d38c46462d40c91cd5a4c80/ebrick_demo/testbench/test_prv32.py#L43-L63) | Common pinned sources feed simulation and physical implementation. | Bind block topology, selected parts and model applicability to the same instance. This 2024 example uses the old `Chip` API; current SiliconCompiler uses `Design` and named project classes. Copying the old calls is not a current integration recipe. |
| [Switchboard Xyce example](https://github.com/zeroasiccorp/switchboard/blob/e38848ad2076f91f61ea0c247d59e20d592c33a1/examples/xyce/test.py#L16-L51), [coupling explanation](https://github.com/zeroasiccorp/switchboard/blob/e38848ad2076f91f61ea0c247d59e20d592c33a1/examples/xyce/README.md#L31-L69) | `SbDut.input_analog` binds a SPICE circuit to explicit digital thresholds; an oversampling clock controls coupling accuracy and cost. | Possible later reset/power-sequencing experiment. Thresholds, time resolution and device models need their own evidence. This is not a PCB placement or routing engine. |

Two counterexamples matter. SiliconCompiler's
[checklist implementation](https://github.com/siliconcompiler/siliconcompiler/blob/059568587c1ffd8ace8442afbef59b01d7732348/siliconcompiler/checklist.py#L309-L479)
can leave criteria without task bindings unevaluated; its optional report/manual
approval controls are not agent-ee's release policy. LogikBench's
[netlist cache key](https://github.com/zeroasiccorp/logikbench/blob/58539b81d9c622a6b55a4cabde5d6b1288b9fe1e/logikbench/common.py#L45-L79)
skips unreadable files and does not itself capture the complete parameters,
toolchain and constraints. Preserve agent-ee's non-vacuous requirement coverage
and source/runtime binding instead of copying either behavior.

## Fit to the current agent-ee implementation

The README's October 25 snapshot is useful orientation, but current code and
the recovery execution records contain newer typed qualification work.
Historical documents disagree about completion at different revisions; none
is fresh qualification of the checkout inspected here.

| Boundary | Existing capability and source | Remaining useful work |
| --- | --- | --- |
| Brief to engineering inputs | [design_dialog](../../src/agent_ee/design_dialog.py), [design_intent](../../src/agent_ee/design_intent.py), [requirement_map](../../src/agent_ee/requirement_map.py), [build_plan](../../src/agent_ee/build_plan.py) capture/validate authored inputs. The [dialog](../design-dialog.md) explicitly makes no model calls or part selections. | Generate a coherent, source-bound bundle from a supported brief, including typed evidence methods. Preserve user constraints and expose unresolved choices. |
| Parts and circuit composition | [parts_stage](../../src/agent_ee/parts_stage.py), [block_library](../../src/agent_ee/block_library.py), [block_adaptation](../../src/agent_ee/block_adaptation.py), [block_variants](../../src/agent_ee/block_variants.py) already provide reusable primitives. | Connect selection, parameter checks and composition to preparation; do not create a second block format or unverified schematic-fragment catalog. |
| Ordinary placement | [placement_intent](../../src/agent_ee/placement_intent.py) declares supported hard fixed/edge/electrical-locality constraints and soft grouping/proximity preferences. [placement_job](../../src/agent_ee/placement_job.py) measures group spread. | Final native group acceptance and broader side/mechanical contracts still need normal-path integration. Numeric thresholds need captured sources or the existing justified-rule policy. |
| Rich block constraints | [block_contract](../../src/agent_ee/block_contract.py) and its [contract](../block-contract.md) already cover role allocation, eligibility, physical endpoints and saved-coordinate evaluation on the candidate-set path. | Adapt this machinery through the prepared Rust job and public build. Preserve routing-stage obligations until actual routed evidence discharges them. |
| Search and recovery | `placement_job._execute_job`, `route` and `_route_feedback` already try a shortlist, share remaining time and perform bounded routing repairs. | Experiment with meaningful strategy branching and failed-route feedback into constrained placement. A general search framework or basic retry loop would duplicate existing work. |
| Electrical evidence | [dc_path](../../src/agent_ee/dc_path.py), [measurements](../../src/agent_ee/measurements.py), native checks and topology/interval analysis already exist. | Connect one supported electrical failure to a bounded design repair, then remeasure the final saved candidate with model limits intact. |
| Release and review | [build_stages._package](../../src/agent_ee/build_stages.py) invokes checks-only review. [qualification](../../src/agent_ee/qualification.py) already enforces typed methods/judgments and exposes separate engineering-review status. [campaign](../../src/agent_ee/campaign.py) has an independent repair/review loop. | Compose the public build with actual independent review when required, reusing current review/campaign machinery and avoiding a new acceptance boolean. |
| Evaluation | [generation benchmark](../generation-benchmark.md), [system acceptance](../system-acceptance.md), [M1 verification matrix](../planning/2026-10-25-recovery/contracts/m1-verification-matrix.md) and existing negative fixtures. | Add a frozen brief-to-package series and record manual interventions. Reuse current mutation cases before adding coverage. Source-only and scripted tests cannot establish live-agent reliability. |

Keep the selected architecture in recovery RD-02: `build_workflow`, prepared
Rust placement and the owned kicad-route adapter. A shared semantic model is a
reason to preserve identities across these boundaries, not evidence that they
must become one process or be replaced by SiliconCompiler.

## Recommended sequence

1. Reuse the current M1 harness and known defects to establish the baseline.
   Complete final group preservation in the ordinary path as existing work.
2. Deliver the acquisition-family brief-to-inputs slice, qualified block
   composition and one controlled part alternative under AGENTEE-525.
3. Connect independent review and bounded repair to the generated candidate.
   Run a small frozen series with an evaluator the generator cannot edit.
4. Extend side/mechanical handling and routing feedback through their existing
   work items. Evaluate tournament search only under matched constraints and
   budgets, after required correctness gates work.
5. Close one supported electrical feedback loop. Expand simulation fidelity
   only when a named requirement cannot be decided by existing models.

The [local work list](2026-10-28-zeroasic-work-items.md) spells out deliverables,
dependencies, negative cases and experiment caps. The
[campaign plan](2026-10-28-zeroasic-campaign-plan.md) refines this sequence into
Plane setup, ordered gates and a finite acceptance series. It qualifies the
reference blocks before brief preparation and develops independent review
alongside it. These proposals map to existing item IDs from the local recovery
records; Plane state was not queried or changed.

## Source coverage and reproducibility

The live blog index contained 47 article links. Twenty relevant entries were
opened/triaged, including the latest October 27 article. Video-only posts were
not treated as analyzed technical evidence. Hiring, funding and older news
coverage supplied no implementation proposals. Additional useful entry points
are [Switchboard's release](https://www.zeroasic.com/blog/switchboard-release),
[Logik's release](https://www.zeroasic.com/blog/logik-release) and
[Building Composable Chiplets](https://www.zeroasic.com/blog/chisic-composable-chiplets).

The public organization inventory contained eight repositories. Five were
cloned, and the linked SiliconCompiler project was cloned separately. The
remaining `cocotbext-umi`, `sbtest` and `zaui` were inspected at README level:
protocol test support, an EDA image and a website framework, respectively.
No board-generation implementation was found there.

Local checkouts are under `~/git/zeroasic-research/`:

| Repository | Inspected commit | Root license / relevant limit |
| --- | --- | --- |
| siliconcompiler/siliconcompiler | `059568587c1ffd8ace8442afbef59b01d7732348` | Apache-2.0 |
| zeroasiccorp/umi | `6c7a519534e0ccd79db8a13643b1d3acc1132b5f` | Apache-2.0 |
| zeroasiccorp/wildebeest | `676398124406ffc62a18dd0624471b950e18046f` | Apache-2.0; README identifies ISC Yosys-derived code |
| zeroasiccorp/logikbench | `58539b81d9c622a6b55a4cabde5d6b1288b9fe1e` | MIT; included benchmark origins need individual review before reuse |
| zeroasiccorp/ebrick-demo | `054d7c4e0377e0412d38c46462d40c91cd5a4c80` | Apache-2.0; older APIs |
| zeroasiccorp/switchboard | `e38848ad2076f91f61ea0c247d59e20d592c33a1` | Apache-2.0 |

These were source inspections, not benchmark reproductions. Selected upstream
Python/JSON files were syntax-parsed; no EDA dependencies were installed and no
native board or upstream simulation was run. This change adds original analysis
and links, without copying upstream code or design assets into agent-ee.
The Pincite release and development checkout were absent on this host, so the
recommendations use inspected repository and primary-source evidence only.
