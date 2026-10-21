# Unified Agentic PCB Placement & Routing: Architectural Blueprint & Recommendations

## 1. Executive Summary & Architectural Vision

Decoupled physical design pipelines—where a placer (`agent-ee`) packs functional blocks into rigid macros based purely on area and pad-to-pad locality, and hands a static board off to a router (`kicad-route`)—inevitably hit deterministic completion ceilings. On dense 2-to-4 layer PCBs, this "throw it over the wall" boundary manifests as two coupled failure modes:
1. **Local Track-Supply Starvation (Placement Blindness):** Packing passive ladders (such as eFuse setting resistors) at $0.26\text{ mm}$ courtyard gaps creates copper-to-copper throats narrower than the minimum required to pass a single track ($w_{\text{track}} + 2s_{\text{clear}} = 0.20\text{ mm} + 2(0.20\text{ mm}) = 0.60\text{ mm}$) or drop a via, creating zero-capacity traps before routing even starts.
2. **Greedy Channel Capture (Routing Myopia):** Single-pass A* routing without persistent spatial pricing allows early-routed nets to cut across high-value pin-escape corridors. Re-ordering failed nets to the front of subsequent passes merely shifts the starvation to 50+ downstream nets.

By **integrating `agent-ee` and `kicad-route` into a unified, neuro-symbolic co-optimization loop**, the system combines the semantic reasoning of an agent with the sub-millisecond geometric precision of constructive C++/Rust/Python solvers. Rather than asking an LLM to output raw $(x, y, \theta)$ coordinates directly, the agent acts as a **Semantic Architect** (pre-placement constraint compilation) and a **Diagnostic Critic** (translating router congestion hotspots into targeted placement and topology mutations).

---

## 2. Three-Tier Integrated System Architecture

To maximize return on engineering effort while preserving the fast, deterministic core of your existing tools, the unified toolchain should be structured as a three-tier closed loop:

```
+-----------------------------------------------------------------------------------+
| TIER 1: SEMANTIC ARCHITECT (Pre-Placement Agent & Schematic Compiler)             |
|  - Parses netlist/datasheets into topological sub-templates (power loops, Kelvin) |
|  - Classifies nets: Vertical Plane-Terminating (GND/+3V3) vs. Horizontal Signal   |
|  - Computes asymmetric per-edge courtyard halos & swappable pin/gate equivalence  |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| TIER 2: CONSTRUCTIVE CO-DESIGN ENGINES (agent-ee + kicad-route)                   |
|                                                                                   |
|  [Phase A: Intra-Block Packer]  --->  [Phase B: Macro Arranger (Soft Boundaries)] |
|   - Per-edge cut-capacity check        - Composite HPWL + Crossing + RUDY score   |
|   - 2-pin ratline crossing pruning     - Boundary passives remain "fluid"         |
|                                                                                   |
|  [Phase C: Escape-Portal Synthesis] -> [Phase D: Negotiated A* Detailed Router]   |
|   - Phase 0 plane-via fanout (GND/3V3) - Persistent 3D history_cost[layer, x, y]  |
|   - 1D DP staggered via ladder escape  - Selective victim rip-up & reroute (RRR)  |
|   - Stub-proximity avoidance halos     - Controlled inner-layer jumper borrowing  |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
| TIER 3: DIAGNOSTIC CRITIC (Cross-Stage Feedback & Mutation Orchestrator)          |
|  - Inspects `analyze_frontier_blocking()`, DRC markers, and `history_cost` peaks  |
|  - Executes targeted mutations:                                                   |
|     1. CRISP-style local courtyard halo inflation -> re-pack offending block      |
|     2. Dynamic pin/gate swapping to untangle non-planar ratline twists            |
|     3. Passive flip (180 deg) or reseat vs. full macro relocation                 |
+-----------------------------------------------------------------------------------+
```

---

## 3. Core Algorithmic Innovations

### 3.1 Placement-Stage Innovations (`agent-ee`)

#### 1. Asymmetric, Pin-Aware Per-Edge Courtyard Halos
Global courtyard inflation fails on fixed-outline boards because it wastes space on component edges with zero signal traffic while under-sizing dense pin edges. Replace static courtyard gaps with an OpenROAD-style per-edge required gap $G_{\text{req}}(\text{edge})$ evaluated during the first-fit check:

$$G_{\text{req}}(\text{edge}) = s_{\text{clear}} + \left\lceil \frac{n_{\text{edge}}}{\eta \cdot L_{\text{eff}}} \right\rceil \cdot (w_{\text{track}} + s_{\text{clear}})$$

* **$n_{\text{edge}}$:** Number of signal nets escaping across that specific component or macro edge (explicitly excluding plane-terminating nets like `GND` and `+3V3` that drop vertically to `In1`/`In2`).
* **$L_{\text{eff}}$:** Effective routing layers available in that corridor ($L_{\text{eff}} = 2$ for `F.Cu` + `B.Cu` in standard regions; $L_{\text{eff}} = 1$ where SMD pads or keep-outs block one surface).
* **$\eta \in [0.65, 0.80]$:** Routing pitch utilization factor accounting for via blockages.
* **Cut-Capacity Verification:** Before committing a passive placement in the backjumping loop, verify that the physical copper-to-copper gap $G_{\text{phys}}$ between adjacent pads satisfies:

$$\left\lfloor \frac{G_{\text{phys}} - s_{\text{clear}}}{w_{\text{track}} + s_{\text{clear}}} \right\rfloor \ge n_{\text{thru}}$$

where $n_{\text{thru}}$ is the number of local nets required to pass between those two components.

#### 2. Constructive Ratline-Crossing Pruning (Cypress-Inspired)
Standard bounding-box congestion estimators (like RUDY) cannot distinguish between two parallel routable traces and two intersecting ("X-crossing") traces that require a layer transition via.
* Inside `agent-ee`'s intra-block packer, whenever a 2-pin passive or child component is tested at candidate rotations $\theta \in \{0^\circ, 90^\circ, 180^\circ, 270^\circ\}$, compute exact 2D line-segment intersections among intra-block signal ratlines (ignoring power/ground plane nets).
* Immediately prune or heavily penalize any orientation $\theta$ that induces a planar X-crossing or forces a ratline segment directly across an adjacent pad's courtyard.

#### 3. Soft Macro Fluidity (De-Freezing Macro Perimeters)
Freezing all 17–19 functional blocks into strictly rigid rectangles before board-level placement traps peripheral nets in suboptimal orientations.
* Partition each functional block into a **Rigid Core** (parent IC, critical high-frequency decoupling loops, matched sense pairs) and a **Fluid Perimeter** (series termination resistors, pull-ups, ESD diodes, inter-block filter passives).
* During macro arrangement, allow fluid perimeter components to slide along the macro boundary or flip $180^\circ$ to align colinearly with the incoming inter-block bus vector, untangling boundary twists before routing begins.

#### 4. Composite Macro Arrangement Objective
Upgrade the top-level macro arranger from pure bounding-box area minimization to a fast composite score (<10 ms per evaluation):

$$C_{\text{macro}} = \alpha \cdot \text{HPWL}_{\text{sig}} + \beta \cdot \text{NC}_{\text{inter}} + \gamma \cdot \frac{1}{|G^*|} \sum_{g_i \in G^*} \text{RUDY}(g_i)$$

where $\text{NC}_{\text{inter}}$ counts inter-macro ratline crossings, and fixed mechanical obstacles (PTH connectors, mounting holes, IMU keep-out rings, RF corridors) are pre-registered in the RUDY grid with unit blockage demand ($\alpha(m) = 1.0$) so macros naturally orient away from choked corridors.

---

### 3.2 Escape & Fanout Synthesis (The Placement-Routing Bridge)

#### 1. Phase 0 Plane-Via Fanout & Escape-Portal Synthesis
General maze routers should not make their first moves inside dense SMD pad fields. Decouple intra-block pin escape from global routing via a dedicated static pre-pass:
* **Immediate Plane Absorption:** Iterate over all `GND` and `+3V3` SMD pads first. Drop local fanout vias directly to `In1` (`GND`) and `In2` (`+3V3`) within a tight radius ($0.60\text{--}0.70\text{ mm}$), removing these nets entirely from the horizontal XY routing problem.
* **Direct Pad-to-Neighbour Pass:** Route ultra-short intra-block pad-to-pad connections (e.g., IC pin to adjacent resistor pad) on `F.Cu` with vias disabled.
* **1D Dynamic Programming Staggered Via Ladders:** For remaining trapped pads in dense passive ladders or QFN footprints, enumerate candidate via sites (on-grid $\rightarrow$ half-grid $\rightarrow$ pad-aligned) in a ring on the block's free side. Solve a 1D compatibility DP along the ladder (enforcing via-to-via spacing $\ge d_{\text{via}} + s_{\text{clear}}$ to automatically stagger adjacent vias), draw the escape stub to the macro perimeter, and register the escaped endpoint as a virtual **Escape Portal**.

---

### 3.3 Routing-Stage Innovations (`kicad-route`)

#### 1. PathFinder Negotiated Congestion & Selective Victim Rip-Up
Eliminate deterministic net-ordering plateaus and cyclic Freerouting-style thrashing by introducing persistent spatial pricing and targeted rip-up:
* **Persistent 3D History Grid:** Maintain `history_cost[layer, x, y]` initialized to $0.0$ across the routing campaign.
* **Soft-Obstacle Fallback Search:** In Pass 1, route all signal nets in ascending $\text{HPWL}$ order with hard obstacle checking. When a net $n_{\text{fail}}$ stalls in A*, invoke `analyze_frontier_blocking()` or run a soft-obstacle A* pass where existing routed tracks/vias of other nets can be crossed at a finite `object_cost` penalty.
* **Targeted Rip-Up & Price Escalation:** Identify the exact set of $k$ victim nets crossed by $n_{\text{fail}}$'s minimum-cost soft path (excluding locked escape stubs and protected nets). Rip up *only* those $k$ victim nets, increment `history_cost[layer, x, y] += h_step` along the contested cells, and re-queue $n_{\text{fail}}$ followed by the $k$ victims.
* **Cost Function:** The A* step cost on resource $r = (\text{layer}, x, y)$ becomes:

$$\text{cost}(r) = \left( b_r + h_r^{(i)} \right) \cdot p_r^{(i)} + \text{stub\_halo}(r)$$

where $b_r$ is the base layer/step cost, $h_r^{(i)}$ is the accumulated history cost at iteration $i$, and $p_r^{(i)}$ is the present congestion penalty schedule.

#### 2. Stub-Proximity Avoidance Halos
To prevent early-routed long buses from hugging unrouted SMD pads and sealing off their escape throats, apply a repulsive cost halo ($\text{stub\_halo}(r) = 3\text{--}5 \times b_{\text{step}}$) around the 1-pitch perimeter of every *unrouted* signal pad (dynamically masked out only for the active source and target pads of the current A* search).

#### 3. Controlled Inner-Layer Plane Borrowing
On a 4-layer stackup (`F.Cu`, `In1=GND`, `In2=+3V3`, `B.Cu`), strictly forbidding signal traces on inner layers causes unnecessary failures when surface layers saturate around wide buses.
* Keep `In1` (`GND`) strictly unbroken to preserve return-path reference integrity.
* Allow `kicad-route` to use `In2` (`+3V3`) for ultra-short "bus-jumper" transitions ($\le 2.5\text{ mm}$ per route) by assigning `In2` a high base step cost multiplier ($2.5\times\text{--}4.0\times$ surface cost) and a hard per-net inner-layer length cap.
* Follow every pass with an automated KiCad DRC zone-continuity check to guarantee that no `In2` jumper splits the $+3\text{V}3$ copper pour into isolated islands or starves a power pin.

#### 4. Topological Bus & River Routing
Group multi-bit parallel buses (e.g., SPI, CAN, memory interfaces) into a single "meta-bus" during initial corridor discovery. Route a single fat spine to reserve a contiguous channel between macros, then unpack the spine into parallel river-routed tracks during detailed assignment so individual bus bits never diverge around obstacles and fragment the board.

---

### 3.4 Agentic Feedback & Cross-Stage Mutations (The Closed Loop)

When the negotiated router still cannot converge after $N$ RRR iterations (e.g., $N = 15$), the **Tier 3 Diagnostic Critic** steps in instead of blindly restarting with a shuffled net order:

1. **CRISP-Style Targeted Halo Back-Annotation:**
   * The Critic extracts the spatial bounding boxes of persistent `history_cost` peaks and frontier stalls from `kicad-route`.
   * It maps those coordinates back to the specific component edges in `agent-ee` and inflates $G_{\text{req}}(\text{edge})$ locally on the offending footprints by $+1$ track pitch ($+0.40\text{ mm}$).
   * Only the affected functional block is re-packed (or locally perturbed via `flip_vs_reseat`), preserving the rest of the board placement.
2. **Schematic-Aware Dynamic Pin & Gate Swapping:**
   * Using schematic metadata, the agent maintains an equivalence map of swappable MCU GPIO pins, connector header pins, resistor-array elements, and multi-gate logic/op-amps.
   * When ratline crossing analysis or router congestion indicates a twisted bus at a macro boundary, the Critic swaps equivalent pins in the netlist in-place, immediately untangling non-planar intersections into planar parallel channels.
3. **Multimodal Visual Critique (Macro-Level Triage):**
   * Render a composite diagnostic image showing macro bounding boxes, unrouted ratline tangles, and the `history_cost` heatmap.
   * Use a Vision-Language Model (VLM) pass strictly for **discrete topological diagnosis** (e.g., *"Macro U4 is rotated 180° away from Connector J2; rotate Macro U4 by 180° and swap placement slots with Macro U7"*), translating high-level visual intuition into discrete solver commands.

---

## 4. Evaluation Metrics & Guardrails

To ensure monotonic progress across integrated placement-routing iterations, wrap the loop in a **Best-Board Preservation Guard** scored by a lexicographical potential function $\Phi$:

$$\Phi(\text{Board}) = \left( N_{\text{clean\_routed}},\; -N_{\text{DRC}},\; -\text{Overflow}_{\text{cut}},\; -\text{WL}_{\text{total}},\; -N_{\text{vias}} \right)$$

* **Ground-Truth Verification:** KiCad 10 native DRC (`kicad-cli pcb drc --format json`) serves as the final arbiter of legality (checking clearance, unrouted nets, silk-to-solder-mask, and copper zone islanding).
* **Pre-Routing Fast Grade:** Before invoking `kicad-route`, `agent-ee` logs three fast (<10 ms) predictors:
  1. **Zero-Capacity Throat Count:** Number of required local signal passages where $G_{\text{phys}} < 0.60\text{ mm}$ (must be $0$ before routing starts).
  2. **Planar Signal Crossing Count ($\text{NC}_{\text{sig}}$):** Total 2-pin ratline intersections excluding plane nets.
  3. **Top-10% RUDY Overflow ($\text{RUDY}_{10\%}$):** Mean congestion across the densest $10\%$ of GCells.

---

## 5. Phased Implementation Roadmap (Ranked by ROI)

| Phase | Target Module | Concrete Architectural Addition | Failure Mode Solved | Est. Effort | Expected Payoff |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Phase 1: Supply Guarantees** | `agent-ee` (Intra-block packer) | **Per-Edge Net-Aware Courtyard Gap ($G_{\text{req}}$) & Cut-Capacity Check** + **2-Pin Ratline Crossing Pruning** during $\theta \in \{0^\circ, 90^\circ, 180^\circ, 270^\circ\}$ evaluation. | Eliminates sub-$0.60\text{ mm}$ zero-capacity courtyard traps and twisted passive orientations at the source. | Low (~100 LOC Python) | **Very High** (Unlocks the 32 intra-block open nets immediately) |
| **Phase 2: Negotiated Routing** | `kicad-route` (A* core & outer loop) | **PathFinder 3D `history_cost` Grid** + **Selective Victim Rip-Up** seeded from `analyze_frontier_blocking()` + **Stub-Proximity Avoidance Halos**. | Breaks the deterministic fixed-point plateau and eliminates greedy bus channel capture without cyclic thrashing. | Moderate (~150 LOC Python/Rust) | **Very High** (Resolves long inter-block bus starvation) |
| **Phase 3: Decoupled Fanout** | `kicad-route` (Pre-pass) | **Phase 0 Plane-Via Fanout** (`GND`/`+3V3` immediate drop) + **1D DP Staggered Via Escape Portals** for dense ladders/ICs. | Prevents maze router from stalling inside dense pin fields and removes plane nets from XY congestion. | Moderate (~120 LOC Python) | **High** (Simplifies global routing into portal-to-portal paths) |
| **Phase 4: Closed-Loop Co-Design** | Unified Orchestrator (`agent-ee` $\leftrightarrow$ `kicad-route`) | **CRISP-Style Courtyard Halo Back-Annotation** (router `history_cost` peaks inflate local $G_{\text{req}}$ and trigger block re-pack / passive flip-vs-reseat). | Automatically heals residual congestion bottlenecks where initial analytic channel estimates were too tight. | Moderate (~150 LOC Python) | **High** (True self-healing placement-routing loop) |
| **Phase 5: Semantic Agentics** | Tier 1 & Tier 3 Agent Layer | **Soft Macro Perimeter Fluidity**, **Dynamic Equivalent Pin/Gate Swapping**, and **Controlled `In2` Jumper Borrowing** with KiCad DRC zone checks. | Untangles topological bus twists at macro boundaries and provides safe relief valves for saturated surface layers. | Moderate–High (~250 LOC Python) | **Medium–High** (Pushes completion toward 100% on Tier B/C boards) |
