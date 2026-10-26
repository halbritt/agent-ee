# Owner request: enforce functional blocks throughout PCB placement

Source: direct owner instructions to agent-529, 2026-10-23. The quoted change
request below defines required behavior and acceptance, not a claim that the
current tool implements it. Tracking: AGENTEE-472. Execution order and current
evidence belong in the [delivery plan](telemetry-pod-delivery-plan.md).

The owner subsequently activated this goal:

> Deliver a manufacturing-ready telemetry pod PCB and make agent-ee a useful,
> reusable tool for agent-driven PCB creation, with the telemetry pod serving
> as its real-world proving case.

The PCB must be fully placed and routed, preserve functional relationships,
satisfy electrical and mechanical requirements, pass independent review of the
final board, and have a verified manufacturing package. Agent-ee must provide a
repeatable workflow carrying declared intent and constraints through placement,
side assignment, routing, validation and manufacturing output. Repairs require
regressions and demonstration on the regenerated board. Both outcomes are required.

## Change request (verbatim)

The placement was supposed to keep related circuitry together in functional blocks. The current board is heavily congested on the top, including around mounting holes and board edges, while most of the underside remains unpopulated.

Fix the reusable placement logic, then regenerate this board.

### Investigate the existing behavior

Trace the existing block definitions through initial placement, side assignment, global optimization, legalization, and any adjustments made in response to routing. Identify where block relationships are ignored, weakened, or lost.

Also determine whether eligible components can actually change sides during optimization, or whether their initial side assignment effectively locks them there. Reuse the existing block model and repair its enforcement throughout the pipeline.

### Required behavior

1. **Make functional blocks first-class placement units.**
   Preserve explicit block membership throughout placement. Every footprint should belong to a declared block or have an explicit role as a shared or fixed component. Shared power and ground nets must not merge otherwise unrelated circuitry into one enormous block.
2. **Use hierarchical placement.**
   Establish fixed mechanical and interface anchors, arrange functional blocks across the available board area and permitted sides, then refine component positions within each block. Blocks may have flexible shapes, but their members must remain physically local and retain their required electrical relationships.
3. **Optimize side assignment.**
   Allow eligible blocks to move between top and bottom as part of placement. Allow a block to span both faces where deliberate vertical adjacency improves its layout. Record actual mechanical, thermal, RF, or assembly restrictions that constrain a block’s side. Optimize connectivity and congestion; an equal component count on each face is not a requirement.
4. **Preserve critical relationships within blocks.**
   Enforce the declared relationships between devices and their decoupling, feedback, filtering, protection, and other supporting components. Measure proximity to the relevant pins or interfaces where appropriate. A small block bounding box alone does not establish good placement.
5. **Keep block constraints active during legalization and routing feedback.**
   Resolving an overlap or routing problem must preserve block cohesion. The engine should move or reshape the affected block, reconsider its side assignment, or report an unsatisfied constraint. Any relaxation of a declared constraint must be explicit and reported.
6. **Apply mechanical keepouts before packing.**
   Account for screw heads, washers, standoffs, connector bodies and access, antenna clearances, and specified enclosure or thermal restrictions. Include component courtyards and height restrictions on the relevant faces.

### Verification and acceptance

- Regenerate this board and provide top and bottom views with functional blocks identified by color or outline.
- Validate block cohesion and critical adjacency constraints against the **final component coordinates**, after all placement adjustments.
- Produce a per-block report showing membership, side assignment, physical spread, estimated internal connection lengths, and any constraint violations or exceptions.
- Demonstrate that eligible underside placement is considered when it improves the constrained layout.
- Confirm that the final placement satisfies the mechanical keepouts.
- Add a regression covering a congested board with available underside space, verifying that block relationships survive global placement and legalization.

Acceptance requires evidence that the intended functional grouping survives into the final board. Routing completion and a clean DRC result are additional checks; neither establishes that the grouping requirement was met.
