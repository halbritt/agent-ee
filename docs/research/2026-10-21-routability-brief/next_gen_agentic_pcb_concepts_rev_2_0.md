# Beyond Classical EDA: 8 Next-Generation Concepts for an Agent-Driven PCB Placer & Router (Rev 2.0)

**Document Revision:** v2.0 — *Next-Horizon Neuro-Symbolic & Physics-Aware Concepts*  
**Target Toolchain:** `agent-ee` (Placer) & `kicad-route` (Router)

Traditional placers and routers treat the schematic netlist as immutable law, treat all copper traces as identical geometric graphs, and treat macro blocks as rigid rectangles. An **agent-driven architecture** breaks all three assumptions. By separating *high-level semantic and topological reasoning* (the Agent) from *fast geometric execution* (`agent-ee` and `kicad-route`), you can implement the following paradigms that exist outside classical deterministic EDA literature.

---

## 1. Schematic-Aware Netlist Mutation (Pin, Gate, & Resistor-Array Swapping)

### The Concept

Classical autorouters fail on dense boards because they take the schematic's pin assignments literally. Yet on almost every real-world PCB, a massive percentage of pins are **functionally swappable**:

* MCU GPIO banks, FPGA I/O banks, and ADC channel inputs.
* Multi-unit logic gates or op-amps (e.g., swapping Unit A and Unit B in an LM358 or quad NAND).
* Pin-1 and Pin-2 on non-polarized 2-pin passives (resistors, ceramic capacitors)—which corresponds to a virtual $180^\circ$ swap or daisy-chain re-ordering.
* Multi-element resistor arrays or connector ground/signal interleaving.

### How the Agent Executes It

1. **Equivalence Group Extraction:** Before placement, the agent parses the schematic and component definitions to tag swappable Equivalence Groups (`swap_group_id`).
2. **Bipartite Barycenter Untangling:** After `agent-ee` places the macros, before `kicad-route` runs A\*, a fast solver (using the **Hungarian algorithm** or **1D Barycenter heuristic** on the boundary ratlines) permutes the netlist assignments within each `swap_group_id` to minimize ratline crossings ($NC \to 0$).
3. **Schematic Back-Annotation:** Once routing succeeds, the agent automatically emits a schematic patch (or KiCad `.kicad_sch` update) so the schematic stays synchronized with the untangled physical layout.

---

## 2. Datasheet-to-Constraint Compilation (Physics-Aware Sub-Layouts)

### The Concept

Human layout engineers spend half their time reading the "Layout Guidelines" section of IC datasheets (e.g., TI, Analog Devices, Infineon eFuses and buck converters). Standard placers treat a switching buck regulator or an eFuse the exact same way they treat an $\text{I}^2\text{C}$ pull-up resistor: via Euclidean $\text{HPWL}$.

### How the Agent Executes It

1. **Multimodal Datasheet Ingestion:** When a new IC is added to the BOM, an LLM/VLM agent reads the PDF datasheet's layout section and compiles a declarative **Topological Constraint Contract (TCC)** in JSON/DSL:
   * **Hot-Loop Polygons:** Identifies high-$dI/dt$ switching loops (e.g., $C_{\text{IN}} \to \text{VIN} \to \text{SW} \to L_1$) and commands `agent-ee` to pack those specific pads for **minimum loop area** on the same layer (`F.Cu`) with zero vias allowed.
   * **Kelvin Sense Pairs:** Identifies current-sense shunt resistors (common in automotive eFuse and BMS designs) and forces `kicad-route` to route the sense lines as a pseudo-differential pair tapping *directly* off the inner pad edges, isolated from the high-current copper pour.
   * **Noisy vs. Quiet Partitioning:** Tags high-$dV/dt$ nodes (like `SW` or `BOOT`) as "aggressor nets," instructing `kicad-route` to project a repulsive keep-out halo onto adjacent analog/feedback (`FB`) nets.

---

## 3. "Liquid Macros" via Constraint-Graph Compaction

### The Concept

Freezing all 17–19 functional blocks as rigid rectangular bounding boxes after Stage 1 wastes board area (L-shaped or T-shaped dead zones inside a block cannot be used by neighboring blocks) and locks peripheral passives onto the wrong side of the macro before the global floorplan is even known.

### How the Agent Executes It

* **Core-Satellite Split:** Instead of freezing the entire functional block, `agent-ee` freezes only the **Anchor Core** (the parent IC + its ultra-critical decoupling/hot-loop passives) as a rigid sub-macro.
* **Orbiting Satellites:** Secondary passives (series termination resistors, pull-ups, ESD diodes, filter caps) remain "liquid satellites" tethered to the Anchor Core by elastic topological springs.
* **Post-Floorplan Compaction:** Once the 17–19 Anchor Cores are placed on the board, a 1D/2D **Constraint-Graph Compactor** (or linear programming legalizer) slides the liquid satellites into the natural interstitial voids *between* macros, automatically aligning them along the vector of the inter-block bus they connect to.

---

## 4. Homotopy / Topological Rubber-Band Routing (CDT)

### The Concept

Grid-based A\* routers (`kicad-route`) suffer from "early geometric commitment": the first net routed picks an exact $(x, y)$ grid coordinate, often cutting a wide channel in half or hugging an obstacle in a way that blocks subsequent parallel tracks. In high-end commercial PCB routers (like Cadence Allegro or TopoR), routing is split into **Topological Homotopy** first, and **Geometric Realization** second.

### How the Agent Executes It

1. **Constrained Delaunay Triangulation (CDT):** Represent all component pads and vias on `F.Cu` and `B.Cu` as vertices in a 2D triangulation mesh. Every triangle edge has an exact physical clearance capacity:

$$
C = \left\lfloor \frac{L_{\text{edge}}}{w_{\text{track}} + s_{\text{clear}}} \right\rfloor
$$

2. **Rubber-Band Sketching:** Route nets across triangle edges without assigning exact $(x, y)$ coordinates—only deciding *which side of each obstacle* a trace passes on (its homotopy class).
3. **Bus Bundling (River Routing):** Multi-bit buses (SPI, CAN, UART, parallel data) are routed as a single "fat" ribbon through the triangulation mesh, guaranteeing zero internal twists and parallel flow.
4. **Geometric Push-and-Shove:** Once the topological ribbons satisfy all triangle edge capacities, convert the rubber-band topology into octilinear KiCad tracks in a single deterministic pass.

---

## 5. Double-Sided "Mirror-Via" Folding (`F.Cu` $\leftrightarrow$ `B.Cu`)

### The Concept

On dense boards, packing all components on a single layer (`F.Cu`) forces passive pads, IC pads, and escape vias to fight for the same surface area. However, a 4-layer PCB is a 3D sandwich.

### How the Agent Executes It

* **Co-Incident Backside Decoupling:** For pin-dense ICs or connectors, `agent-ee` can selectively flip decoupling capacitors and pull-up/setting resistors directly onto `B.Cu` (the underside of the board) right beneath the parent IC.
* **Shared Mirror Vias:** A single through-hole via dropped next to an `F.Cu` IC pad travels straight down into the `B.Cu` passive pad (or sits $0.30\text{ mm}$ away on `B.Cu`), completely vacating the `F.Cu` routing channel around the IC so signal traces can escape unhindered on the top layer.
* **Agent Trade-Off Reasoning:** Because double-sided assembly adds a second SMT reflow pass (increasing manufacturing cost), the agent only invokes `B.Cu` folding when local `F.Cu` escape capacity falls below demand.

---

## 6. Continuous Plane-Return & Impedance Integrity Critic

### The Concept

No public academic PCB placer/router explicitly optimizes power/ground plane continuity. Worse, when signals transition between `F.Cu` and `B.Cu` via a through-hole, two insidious electrical failures happen on 4-layer boards:

1. **Via Anti-Pad Swiss-Cheesing:** Dropping a row of adjacent vias punches overlapping anti-pad clearance holes through the `In1` (`GND`) and `In2` (`+3V3`) copper pours, choking DC current capacity and creating high-inductance return bottlenecks.
2. **Return-Path Discontinuity:** When a high-speed signal jumps from `F.Cu` (referenced to `In1` `GND`) to `B.Cu` (referenced to `In2` `+3V3`), its AC return current has no low-impedance path between planes unless a decoupling capacitor or stitching via is nearby.

### How the Agent Executes It

* **Anti-Pad Trench Penalty in A\*:** Whenever `kicad-route` evaluates placing a via at $(x, y)$, it checks if that via's anti-pad merges with a neighboring via's anti-pad on `In1`/`In2`. Merging anti-pads into a continuous slot incurs a progressive cost penalty unless separated by a minimum web of copper (e.g., $\ge 0.25\text{ mm}$).
* **Automated Stitching-Via Injection:** After routing, the agent runs a 2D flood-fill connectivity and neck-width analyzer on the `In1` and `In2` copper zones. Anywhere a ground pour peninsula is isolated or a high-speed signal transitions layers far from a decoupling cap, the agent autonomously drops `GND` stitching vias into empty grid space.

---

## 7. Multimodal "Visual + Spatial DSL" Critic Loop

### The Concept

LLMs are ineffective at arithmetic over thousands of $(x, y)$ coordinates, which is why pure LLM placers fail. However, Vision-Language Models (VLMs) combined with a **Spatial Domain-Specific Language (DSL)** excel at diagnosing macro-level topological pathologies that local greedy heuristics miss.

### How the Agent Executes It

1. **Multi-Channel Diagnostic Rendering:** After a fast placement + global routing pass (taking $<10\text{ s}$), the toolchain renders a clean, high-contrast schematic-layout overlay showing:
   * Functional macro bounding boxes and orientations.
   * Color-coded ratline bundles (inter-block buses) and congestion hotspots (`history_cost` heatmaps).
2. **High-Level Topological Critique:** The VLM/Agent inspects both the visual render and a compact topological summary graph (e.g., *"Block_MCU has 14 nets crossing Block_Power to reach Block_Connector; 9 ratlines intersect at* $(42.5, 31.0)$*"*).
3. **DSL Action Space:** Instead of outputting raw coordinates, the agent issues high-level **Layout Directives** to `agent-ee`:
   * `SWAP_MACRO_POSITIONS(Block_A, Block_B)`
   * `ROTATE_MACRO(Block_C, 180)`
   * `RESERVE_BUS_HIGHWAY(from=Block_MCU, to=Block_Conn, width=3.5mm, layer=F.Cu)`
   * `ALLOW_BACKSIDE_FOLDING(Block_eFuse, passives=[R12, R14])`

---

## 8. Manufacturability (DFM) & Thermal-Field Co-Placement

### The Concept

A board that passes 100% DRC completion in `kicad-route` can still fail in production or burn up on the bench. An agent-driven tool can embed physical manufacturing and thermal physics into the constructive packer at near-zero runtime cost:

* **Reflow Tombstoning Prevention (Thermal Symmetry):** When `kicad-route` connects a small 0402/0603 passive to a wide track on one pad and a narrow track (or direct via) on the other, uneven solder-paste melting causes the part to "tombstone" during reflow. `kicad-route` can enforce symmetric pad-entry trace widths and thermal-relief spokes for small passives.
* **Fast 2D Poisson Thermal Spreading:** For power-dissipating blocks (like automotive eFuses, LDOs, or MOSFETs), `agent-ee` can run a millisecond 2D finite-difference thermal kernel (or repulsion field) that prevents two high-wattage ICs from being packed edge-to-edge, while automatically reserving copper-pour thermal-via arrays under exposed thermal pads (`EPAD`) before signal routing starts.
* **Automated Silkscreen & Probe-Point Legalization:** As a final post-routing pass, the agent slides overlapping reference designators (`R1`, `C4`) onto clear silkscreen areas using a fast bounding-box placer and drops accessible test-point pads on critical nets (`GND`, `+3V3`, `FAULT`, `CAN_H/L`) for bench bring-up.

---

## Summary Comparison: Classical EDA vs. Agent-Driven EDA

| Dimension | Classical EDA (Rev 1.0 / Existing Reports) | Agent-Driven Innovation (Rev 2.0 / Above & Beyond) |
| :--- | :--- | :--- |
| **Netlist** | Frozen input; router must obey every pin assignment. | **Mutable within Equivalence Groups** (GPIO, gate, and passive pin swapping). |
| **Component Semantics** | Blind geometry; all nets weighted by $\text{HPWL}$ or pin count. | **Datasheet-compiled constraints** (tight switching loops, Kelvin sense pairs). |
| **Macro Hierarchy** | Rigid bounding boxes after Stage 1. | **Liquid Core-Satellite macros** that compact into inter-block voids. |
| **Assembly Dimension** | Strictly 2D planar packing on `F.Cu`. | **3D Mirror-Via folding** onto `B.Cu` for dense pin fields. |
| **Routing Representation** | Immediate grid-coordinate commitment (A\*). | **Topological rubber-band homotopy (CDT)** & river-routed bus ribbons. |
| **Power Planes (`In1`/`In2`)** | Passive layers or penalized jumper layers. | **Active anti-pad slot prevention** + automated `GND` stitching-via injection. |
| **Agent's Role** | None (pure C++/Python/Rust heuristics). | **Neuro-Symbolic Orchestrator** issuing high-level DSL directives from visual/graph diagnostics. |