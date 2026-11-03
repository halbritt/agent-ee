# Quickstart: Generating Your First PCB with agent-ee

This guide walks you through generating a manufacturing-ready printed circuit board (PCB) from an engineering build plan in under five minutes.

`agent-ee` is a deterministic, multi-stage generative EDA pipeline. It takes structured engineering intent (requirements, component specifications, board envelope, and placement constraints) and synthesizes native KiCad schematics, routed boards, 3D renderings, and sealed manufacturing fabrication packages (Gerbers, drill files, BOM, and CPL).

---

## 1. Prerequisites and Preflight Check

Before running a build, verify that all required native EDA tools and binaries are installed and accessible. Run the built-in diagnostic probe:

```bash
./bin/agent-ee doctor --checks-only
```

The `--checks-only` flag probes native compilation tools without checking for optional Large Language Model (LLM) reviewer harnesses (`claude`, `codex`, `agy`):

* **KiCad CLI (`kicad-cli`)**: Version 10.0.7 or later (used for ERC, DRC, and export operations).
* **Native Python Bindings (`pcbnew`)**: The native C++ extension installed with KiCad (e.g., in `/usr/lib/python3/dist-packages/_pcbnew.so`).
* **Placement Engine (`agent-ee-placement`)**: The compiled Rust geometric solver located at `/usr/local/libexec/agent-ee/agent-ee-placement` (or overridden via `AGENT_EE_RUST_PLANNER`).
* **Simulation Engine (`ngspice`)**: Version 45 or later (used for active SPICE checks).
* **Process Sandboxing (`bwrap`)**: Bubblewrap 0.11 or later (used to isolate tool runs with read-only candidate mounts).
* **PDF Utility (`pdftotext`)**: Poppler 26 or later (used to extract datasheet text).

When all checks pass, `agent-ee doctor` outputs a structured JSON report and exits with code `0`.

---

## 2. Anatomy of a Minimal Build Plan

The build generator consumes a JSON build plan adhering to the `agent-ee.build-plan/2` (or `agent-ee.build-plan/1`) schema. A build plan specifies where source inputs reside and maps each pipeline stage to input files:

```json
{
  "schema": "agent-ee.build-plan/2",
  "source": "inputs",
  "envelope": "envelope.json",
  "requirements": {
    "map": "requirement-map.json",
    "record": "spec-record.json",
    "spec": "spec.md",
    "obligations": "obligations.json"
  },
  "parts": {
    "manifest": "parts.json",
    "circuit": "circuit.json",
    "layout": "layout.json"
  },
  "placement": {
    "intent": "placement-intent.json",
    "groups": "groups.json",
    "budget_s": 600,
    "search": {
      "seeds": [1, 2, 3, 4],
      "steps_per_seed": 100000,
      "shortlist": 2
    },
    "router": {
      "checkout": "/opt/kicad-route"
    }
  }
}
```

Key configuration sections:
* **`envelope`**: Defines physical board geometry (width, height, edge cut vertices, and keepout boundaries).
* **`requirements`**: Tracks natural-language requirements in Markdown (`spec.md`) and maps them to executable acceptance checks (`requirement-map.json`).
* **`parts`**: Defines selected components, vendor part numbers (e.g., LCSC), KiCad symbol identifiers, and footprint libraries.
* **`placement`**: Configures the Rust geometric optimizer search budget, random seeds, and router parameters.

---

## 3. Running the Generator

To execute the 7-stage manufacturing build pipeline, run `agent-ee build run` pointing to your build plan and specifying an output run directory:

```bash
./bin/agent-ee build run bundles/i2c/build-plan.json --out runs/quickstart-run
```

As execution proceeds, `agent-ee` coordinates the 7 sequential pipeline stages:

```
[01 requirements]       Parsing requirements and establishing check baselines...
[02 parts]              Compiling part manifest and binding footprint land patterns...
[03 scaffold]           Generating native .kicad_pcb envelope, layer stackup, and netclasses...
[04 schematic]          Emitting hierarchical .kicad_sch, annotating footprints, and running native ERC...
[05 placement-routing]  Executing Rust placement solver and kicad-route autorouter...
[06 artwork]            Resolving silkscreen collisions and adjusting reference designators...
[07 package]            Executing checks-only gate (ERC, DRC, DFM) and exporting Gerbers, drills, BOM, CPL...
```

Each stage consumes the immutable artifacts produced by earlier stages. If all native checks (ERC, DRC, netlist parity) pass without violations, the command finishes with status `accepted` and exits with code `0`.

---

## 4. Inspecting the Candidate Outputs

All generated files are sealed within the run output directory:

```bash
ls -la runs/quickstart-run/package/candidate/
```

Key output files:
* **Native CAD Files**:
  * `*.kicad_sch`: Fully annotated, native KiCad 10 schematic with complete netlist connectivity.
  * `*.kicad_pcb`: Fully routed PCB layout with copper fills, thermal reliefs, and design-rule compliant clearances.
  * `*.kicad_pro`: KiCad project configuration.
  * `*.kicad_dru`: Native KiCad design rules file.
* **Manufacturing Fabrication Package**:
  * `*.gbr`: RS-274X Gerber files for copper layers (`F.Cu`, `B.Cu`), soldermask (`F.Mask`, `B.Mask`), silkscreen (`F.Silkscreen`, `B.Silkscreen`), and board edge (`Edge.Cuts`).
  * `*.drl`: Excellon NC drill files for plated through-holes and vias.
  * `BOM.csv`: Bill of Materials mapping reference designators to manufacturer part numbers and LCSC order codes.
  * `CPL.csv`: Component Placement List (Pick-and-Place) with exact X, Y coordinates, rotation, and board side.
* **Custody and Qualification Records**:
  * `accepted-package.json`: Contains the cryptographic SHA-256 hashes of all exported fabrication artifacts.
  * `build-report.json`: Comprehensive report recording stage receipts, runtime fingerprints, and check outcomes.

---

## 5. Visualizing the Result in 3D

`agent-ee` provides built-in 3D visualization tools that do not require opening the KiCad GUI.

### Interactive Web Viewer (`view3d`)

Generate a standalone Three.js 3D web bundle to inspect the board, components, and layer stack in your browser:

```bash
./bin/agent-ee view3d runs/quickstart-run/package/candidate/board.kicad_pcb --out /tmp/board-viewer --port 8080 --no-browser
```

To export the web viewer files without launching an HTTP server:

```bash
./bin/agent-ee view3d runs/quickstart-run/package/candidate/board.kicad_pcb --out build/viewer-export --export-only
```

### Photorealistic Ray-Traced Renders (`render3d`)

To generate publication-grade ray-traced PNG renderings (top, bottom, and isometric views) using the native KiCad raytracer:

```bash
./bin/agent-ee render3d runs/quickstart-run/package/candidate/board.kicad_pcb --out renders/quickstart --quality basic --jobs 3
```

This exports high-resolution images (`top.png`, `bottom.png`, `isometric.png`) to `renders/quickstart/` along with an audit manifest.

---

## 6. Next Steps

* **Production Operations**: Read the [Operator Runbook](operator-runbook.md) for instructions on checkpoints (`--through`), resuming interrupted builds (`--resume`), and production container deployment.
* **Pipeline Architecture**: Read the [Architecture & Pipeline Guide](architecture-pipeline.md) for a technical deep-dive into candidate immutability, the Rust placement solver, and Bubblewrap sandboxing.
* **CLI Reference**: Consult the [CLI Reference](cli-reference.md) for the complete list of all subcommands, options, and exit codes.
* **Troubleshooting**: If a stage fails or reports an ERC/DRC violation, see the [Generator Troubleshooting & FAQ](troubleshooting-faq.md).
