# Architecture & 7-Stage Generative Pipeline Guide

`agent-ee` is an automated, deterministic electronic design automation (EDA) system that synthesizes complete, manufacturing-ready printed circuit board (PCB) packages from structured engineering intent.

This document describes the architectural principles, the 7-stage generative synthesis pipeline, data flow and candidate immutability, Bubblewrap execution sandboxing, and subsystem interfaces.

---

## 1. Architectural Philosophy and Design Principles

1. **Synthesis from Explicit Engineering Intent**: `agent-ee` treats circuit design as an engineering compiler problem. Rather than relying on human interactive CAD sessions or ambiguous free-form prompts, inputs are rigorously structured JSON and Markdown documents defining functional requirements, component choices, electrical connectivity, board envelopes, and placement intent.
2. **Candidate Immutability and Byte-Level Provenance**: Every intermediate board state is an immutable candidate snapshot identified by its cryptographic SHA-256 tree digest (`tree_identity`). Pipeline stages never perform in-place mutations on previous stage artifacts. Instead, each stage consumes read-only files from the prior stage, produces new artifacts in a dedicated receipt directory, and formally adopts the new candidate state.
3. **Decoupled Verification (Checks-Only Gate)**: Design acceptance never trusts self-reported tool status. Final qualification runs native KiCad CLI processes (`kicad-cli sch erc` and `kicad-cli pcb drc`) and independent physical verification checks against the adopted candidate files.
4. **Hybrid Coordination Architecture**: High-level stage coordination, requirement mapping, and EDA data modeling are implemented in Python. Computationally intensive geometric search and thermal placement optimization are implemented in a standalone, high-performance Rust engine (`agent-ee-placement`).

---

## 2. The 7 Pipeline Stages

The manufacturing build pipeline executes seven sequential stages defined in `agent_ee.retained_run.STAGES`:

```
Stage 1: Requirements  -->  Stage 2: Parts  -->  Stage 3: Scaffold  -->  Stage 4: Schematic
                                                                                |
Stage 7: Package       <--  Stage 6: Artwork <--  Stage 5: Placement & Routing <--+
```

### Stage 1: Requirements (`requirements`)
* **Primary Inputs**: `spec.md`, `spec-record.json`, `requirement-map.json`, optional `obligations.json`.
* **Operations**:
  * Validates the SHA-256 digest of the requirement specification against `spec-record.json`.
  * Verifies exact byte-range locators mapping each requirement ID to its definition in `spec.md`.
  * Evaluates `requirement-map.json` to map each requirement to executable acceptance checks (e.g., ERC, DRC, SPICE, netlist parity, physical obligations).
* **Outputs**: Initial stage scope and accounting records (`retained_run.write_scope`, `retained_run.write_accounting`).

### Stage 2: Parts (`parts`)
* **Primary Inputs**: `parts.json` (component manifest), `circuit.json` (connectivity), `layout.json` (layout constraints), optional passive ratings.
* **Operations**:
  * Resolves component symbols from captured KiCad `.kicad_sym` libraries and footprint land patterns from `.kicad_mod` libraries.
  * Validates pin names, pin numbers, and electrical types against manufacturer datasheets.
  * Binds component ratings and establishes derivation lineage (`build-generated/parts-ratings/`).
* **Outputs**: Normalized connectivity intent (`build-generated/connectivity-intent.json`) and adopted parts receipt.

### Stage 3: Scaffold (`scaffold`)
* **Primary Inputs**: `envelope.json` (board outline coordinates), `parts.json`, `build-generated/connectivity-intent.json`.
* **Operations**:
  * Generates the native KiCad project file (`.kicad_pro`) and empty board file (`.kicad_pcb`).
  * Establishes the physical board contour (`Edge.Cuts`), layer stackup (layer count, copper weight, substrate thickness), and netclasses.
  * Writes native design rules (`.kicad_dru`) defining clearance constraints.
  * Fixes edge connector footprints at their specified millimeter locations and orientations.
* **Outputs**: Scaffolded project candidate and compiled acceptance baseline (`scaffold-receipt.json`).

### Stage 4: Schematic (`schematic`)
* **Primary Inputs**: `build-generated/connectivity-intent.json`, `layout.json`, captured symbol libraries.
* **Operations**:
  * Emits native KiCad 10 hierarchical schematics (`.kicad_sch`).
  * Forward-annotates component reference designators, footprint assignments, and sheet hierarchies.
  * Executes native KiCad Electrical Rules Check (`kicad-cli sch erc`), filtering known coordinate scaling quirks via `kicad_quirks.py`.
* **Outputs**: Emitted schematic files, ERC report, and forward-annotated netlist.

### Stage 5: Placement & Routing (`placement-routing`)
* **Primary Inputs**: Candidate `.kicad_pcb`, `placement-intent.json`, `groups.json`, router checkout (`/opt/kicad-route`).
* **Operations**:
  * **Placement**: The Python coordinator converts board geometry, footprint shapes, and netlists into `agent-ee.placement-job-input/2` (in integer nanometres). The Rust placement engine (`agent-ee-placement`) runs multi-seed simulated annealing to optimize component locations against a multi-objective cost function (wirelength, group clustering, proximity, link alignment, escape space, and overlap penalties).
  * **Plane Fills**: Generates copper zones and ground plane pours (`F.Cu`, `B.Cu`).
  * **Routing**: Coordinates routing with the pinned `kicad-route` engine in an isolated Bubblewrap step.
  * **Verification**: Executes native KiCad DRC (`kicad-cli pcb drc`), requiring 0 unrouted nets, 0 track dangling errors, and 0 clearance violations.
* **Outputs**: Fully placed and routed `.kicad_pcb` board file.

### Stage 6: Artwork (`artwork`)
* **Primary Inputs**: Routed `.kicad_pcb` board file.
* **Operations**:
  * Detects silkscreen text collisions with exposed copper pads (`silk_over_copper`) and board edges.
  * Executes a bounded native producer that shifts reference designator text fields without violating text clearance boundaries.
* **Outputs**: Adjusted `.kicad_pcb` with resolved silkscreen conflicts.

### Stage 7: Package (`package`)
* **Primary Inputs**: Final `.kicad_pcb`, `.kicad_sch`, project rules, review profile.
* **Operations**:
  * Executes the checks-only gate: full ERC, DRC, netlist parity, DFM checks, and assembly readiness.
  * Exports manufacturing fabrication files:
    * RS-274X Gerbers (`.gbr`) for all copper, mask, silk, and edge layers.
    * Excellon NC drill files (`.drl`).
    * Bill of Materials (`BOM.csv`).
    * Component Placement List (`CPL.csv`).
* **Outputs**: `accepted-package.json` sealing the candidate files and manufacturing package with SHA-256 digests (inside `stages/07-package-*/accepted-package.json`).

---

## 3. Data Flow and Directory Layout

A build run directory enforces a strict structure separating captured inputs, immutable candidates, execution receipts, and scratch directories:

```
<run-dir>/
├── .lock                     # Shared/exclusive file lock (fcntl.flock)
├── build-report.json         # Overall build qualification report (records active candidate path)
├── source/                   # Captured preflight inputs and source inventory copy
├── stages/                   # Isolated stage directories (01-requirements through 07-package)
│   ├── <index>-<stage>-<hex>/
│   │   ├── candidate/        # Immutable candidate files produced by this stage
│   │   │   ├── board.kicad_pcb
│   │   │   ├── board.kicad_sch
│   │   │   ├── board.kicad_pro
│   │   │   └── board.kicad_dru
│   │   ├── accepted-package.json # Sealed manufacturing package manifest (stage 07)
│   │   ├── receipts/         # Step receipts and execution logs for this stage
│   │   │   ├── 0/
│   │   │   │   ├── receipt.json
│   │   │   │   └── run/
│   │   │   │       ├── execution.json
│   │   │   │       ├── stdout.log
│   │   │   │       ├── stderr.log
│   │   │   │       └── console.log
│   │   │   └── ...
│   │   └── work/             # Isolated scratch workspace used during active tool runs
│   └── ...
└── superseded/               # Historical records preserved when resuming or replacing
```

### File Locking and Writer Exclusivity
To prevent concurrent runs from corrupting candidate files, `agent-ee` acquires a non-blocking file lock (`fcntl.flock`) on `.lock`. If another process owns the lock, invocation raises `ValueError('another writer owns the build')`.

---

## 4. Sandboxing and Execution Isolation

All native tools (`kicad-cli`, `ngspice`, python scripts) run inside an isolated Linux container sandbox created via Bubblewrap (`bwrap`).

```
+-----------------------------------------------------------+
| Host Filesystem                                           |
|   /usr (read-only) ---------------> /usr (in sandbox)     |
|   <inputs> (read-only) -----------> /candidate (in sb)    |
|   <app-code> (read-only) ---------> /app (in sandbox)     |
|   <work-dir> (writable) ----------> /work (in sandbox)    |
+-----------------------------------------------------------+
```

### Sandbox Mount Configuration (`executor.sandbox_argv`)
* `/usr`, `/lib`, `/lib64`, `/bin`, `/sbin`: Mounted read-only from the host.
* `/candidate`: Mounted read-only (`--ro-bind`) pointing to the current candidate directory. Tools cannot modify input files.
* `/app`: Mounted read-only pointing to the `agent-ee` Python package root.
* `/work`: A clean, private scratch directory mounted read-write (`--bind`). The process working directory is set to `/work`.
* `/tmp`: A private in-memory tmpfs (`--tmpfs /tmp`).
* `--clearenv`: All host environment variables are stripped. Only strict, controlled variables are set:
  * `PATH`: `/usr/local/bin:/usr/bin:/bin`
  * `HOME`: `/work/home`
  * `LANG`: `C.UTF-8`
  * `PYTHONPATH`: `/app`
  * `KICAD_CONFIG_HOME`: `/work/kicad-config`
  * `KICAD10_SYMBOL_DIR`: `/candidate/stock/symbols`
  * `KICAD10_FOOTPRINT_DIR`: `/candidate/stock/footprints`
* `--unshare-net`: Network namespaces are unshared by default. Native builds run completely networkless.
* `--unshare-pid`, `--unshare-ipc`, `--unshare-uts`: Isolates processes, inter-process communication, and hostnames.

### Mount Identity Guard (`MOUNT_GUARD`)
To prevent race conditions where host mount paths change between resolution and execution, `bwrap` executes a Python prelude that checks the device and inode numbers (`st_dev`, `st_ino`) of every mounted descriptor. If any discrepancy is found, it aborts with exit code `125` (`mount_source_changed`).

---

## 5. Subsystem Interfaces

### Python Coordinator <-> Rust Placement Engine
The Python coordinator communicates with the Rust placement engine (`agent-ee-placement`) via typed JSON interfaces:

* **Input Interface (`agent-ee.placement-job-input/2`)**:
  * Generated by `src/agent_ee/placement_problem.py`.
  * All dimensions are normalized to integer nanometres (`NM = 1_000_000` per mm):
    * `outline_rect_nm`: Board bounding box in nanometres.
    * `rules`: Clearance rules (`copper_clearance_nm`, `hole_to_hole_clearance_nm`, `copper_edge_clearance_nm`, `mask_min_web_nm`).
    * `pair`: Differential pair routing rules (`width_nm`, `gap_nm`, `clearance_nm`).
    * `footprints`: Component records with bounding rectangles, pad coordinates, and occupancy shapes.
    * `weights`: Cost function weights (`wire: 1`, `group: 1`, `proximity: 4`, `link: 1`, `edge: 4`, `hard: 50`, `escape: 20`).
* **Output Interface (`agent-ee.placement-job-output/1`)**:
  * Emitted by `agent-ee-placement`.
  * Carries `problem_sha256` matching the canonical input problem digest.
  * Contains optimized X, Y nanometre coordinates and quarter-turn rotations for all placed components.

### Python Coordinator <-> KiCad Route Adapter
Defined in `src/agent_ee/routing_adapter.py`:
* **Input Step**: Prepares an `agent-ee.stage-step/1` declaration mounting the pinned `kicad-route` checkout at `/opt/kicad-route`.
* **Execution**: Executes the routing solver within the Bubblewrap sandbox.
* **Verification**: The adapter re-reads the output board and executes native KiCad DRC checks to independently confirm track clearances and netlist connectivity before adopting the candidate.

---

## 6. Related Documentation

* [Quickstart Guide](quickstart.md): Getting started with your first PCB build.
* [Operator Runbook](operator-runbook.md): Production operations, checkpoints, and container deployment.
* [Benchmarking Guide](benchmarking-guide.md): Evaluating placement engine and dual-gate benchmark performance.
* [Generator Troubleshooting & FAQ](troubleshooting-faq.md): Diagnostics for stage errors and DRC failures.
* [CLI Reference](cli-reference.md): Complete list of subcommands and flags.
