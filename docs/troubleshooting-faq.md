# Generator Troubleshooting & Frequently Asked Questions (FAQ)

This guide provides diagnostic procedures, failure resolution matrices, and frequently asked questions for the `agent-ee` generative PCB synthesis pipeline.

---

## 1. Stage-by-Stage Failure Matrix

During execution of `agent-ee build run`, stages execute sequentially. If a stage fails or reports an invalid condition, execution stops immediately, recording the cause in `<run-dir>/receipts/<index>/run/console.log` and `<run-dir>/build-report.json`.

| Pipeline Stage | Common Error or Symptom | Root Cause | Remedial Action |
|---|---|---|---|
| **Requirements** | `spec_record_sha256 names different bytes` | `requirement-map.json` references an outdated `spec-record.json` hash. | Recompile `spec-record.json` from `spec.md` and update `spec_record_sha256`. |
| **Requirements** | `missing requirement ID in spec` | A requirement ID in `requirement-map.json` is not defined in `spec.md`. | Ensure all requirement keys match exact markdown headings in `spec.md`. |
| **Parts** | `unmapped footprint or symbol` | Component references a symbol or footprint library not captured locally. | Run `agent-ee design capture` or ensure symbols exist in `/candidate/stock/`. |
| **Parts** | `repeated_decoupling_lands` | Decoupling specification assigns multiple capacitors to the same pad. | Disambiguate bypass pin pairs in `decoupling` block in `circuit.json`. |
| **Parts** | `stale stock evidence` | Component stock quotes or price evidence are older than 24 hours. | Recapture supplier quotes using `agent-ee design capture`. |
| **Scaffold** | `polygon self-intersection` | Board outline vertices in `envelope.json` cross each other. | Ensure coordinates in `envelope.json` define a simple, non-intersecting polygon. |
| **Scaffold** | `invalid copper layer count` | Requested layer count is not supported (e.g., odd number or > 6). | Set `copper_layers` in `board` configuration to `2`, `4`, or `6`. |
| **Schematic** | `ERC error: pin_not_connected` | An active component pin is neither connected to a net nor in `no_connects`. | Add the pin to `nets` or mark it in the `no_connects` list. |
| **Schematic** | `ERC error: power_pin_not_driven` | Power net lacks a driving power flag symbol. | Add the net name to `power_flags` in your design specification. |
| **Placement** | `placement solver timeout` | Placement engine exhausted `budget_s` without resolving overlaps. | Increase `budget_s` or `steps_per_seed`, or enlarge board envelope. |
| **Placement** | `component outside envelope` | Footprint geometry and clearance exceed board boundary. | Move fixed connectors inward or increase board width/height. |
| **Routing** | `unrouted connections (open_tracks)` | Routing channels congested; router could not complete all nets. | Increase layer count (e.g. 2 to 4), widen spacing, or increase timeout. |
| **Routing** | `DRC clearance violation` | Copper tracks violate spacing constraints in `.kicad_dru`. | Adjust netclass clearance rules or relax spacing in `layout.json`. |
| **Artwork** | `silkscreen collision (silk_over_copper)` | Component reference designator overlaps an exposed solder pad. | Adjust silkscreen margins or shift reference designator text fields. |
| **Package** | `hash mismatch on exported Gerbers` | Candidate files were modified while package checks were executing. | Verify directory immutability; ensure no external process touches `<run-dir>`. |

---

## 2. Detailed Diagnostic Walk-Throughs

### Requirements Stage Failures

#### Problem: Spec Record SHA-256 Mismatch
```
ValueError: requirement map names different spec-record bytes
```
* **Cause**: The cryptographic hash of `spec-record.json` declared in `requirement-map.json` does not match the actual file contents.
* **Resolution**: Recompute the hash of `spec-record.json` using `sha256sum spec-record.json` and update the `spec_record_sha256` field in `requirement-map.json`.

---

### Parts Stage Failures

#### Problem: Repeated Decoupling Lands
```
ValueError: repeated decoupling lands are refused with a specific remedy
```
* **Cause**: In `agent-ee.design/1` or `circuit.json`, two decoupling capacitors are bound to the same IC power/ground terminal without distinct terminal definitions.
* **Resolution**: In the `decoupling` array, ensure each decoupling capacitor specifies distinct supply and return pin terminals (e.g., `["U1.1", "C1.1"]` and `["U1.8", "C2.1"]`).

---

### Schematic Emission Failures

#### Problem: Power Pin Not Driven (`power_pin_not_driven`)
```
ERC error: Input Power pin not driven by any Output Power pins (Net: +3.3V)
```
* **Cause**: KiCad ERC requires that power nets have a driving source symbol (such as a regulator output or virtual `PWR_FLAG`).
* **Resolution**: In `agent-ee.design/1`, add the net name (e.g., `"+3.3V"` or `"VCC"`) to the `power_flags` array:
  ```json
  {
    "power_flags": ["+3.3V", "GND"]
  }
  ```

#### Problem: KiCad 10 Coordinate Scaling Quirk
* **Symptom**: ERC report coordinates do not line up with schematic components.
* **Cause**: KiCad 10.0.6 suffered from an upstream issue where ERC JSON report coordinates were scaled down by 100x.
* **Resolution**: `agent-ee` includes an automated quirk adapter in `src/agent_ee/kicad_quirks.py`. When running on KiCad 10.0.6, the coordinate regime is detected as `scale_x100` and coordinates are automatically normalized. On KiCad 10.0.7+, the regime is detected as `native` without scaling. Ensure your KiCad version is accurately reported by `agent-ee doctor`.

---

### Placement Solver Failures

#### Problem: Placement Convergence Timeout
```
ValueError: placement/routing did not pass: timeout
```
* **Cause**: The Rust placement engine (`agent-ee-placement`) was unable to find an overlap-free configuration within the allotted `budget_s`.
* **Resolution**:
  1. In `build-plan.json`, increase `placement.budget_s` (e.g., from 600 to 1200 seconds).
  2. Increase `placement.search.steps_per_seed` (e.g., from 100,000 to 200,000).
  3. Verify that the board area in `envelope.json` provides adequate surface area for the total component footprint footprint area (a component-to-board area ratio under 60% is recommended).

---

### Routing Failures

#### Problem: Unrouted Connections (`open_tracks`)
```
ValueError: router reported 2 unrouted net(s)
```
* **Cause**: The board topology is too congested for the autorouter to complete tracks on the configured number of copper layers.
* **Resolution**:
  1. Increase the board layer count from 2 to 4 layers in `board.copper_layers`.
  2. In `layout.json`, reduce the default trace width and clearance rules if supported by your PCB fabricator (e.g., from 0.25 mm to 0.15 mm).
  3. Increase the router search timeout in `build-plan.json`.

---

### Artwork Stage Failures

#### Problem: Silkscreen Collision with Exposed Copper
```
ValueError: artwork retains 1 native silkscreen conflict(s); see runs/run-1/artwork/artwork.json
```
* **Cause**: Silkscreen text (such as component reference designator `R1` or polarity indicators) overlaps exposed solder pads, violating fabrication DFM rules.
* **Resolution**: Inspect `<run-dir>/artwork/artwork.json` to identify the colliding reference designators. Adjust component text placement or decrease the font size in `layout.json`.

---

## 3. System and Environment Diagnostics

### Problem: `pcbnew native extension unavailable`
```
ValueError: pcbnew native extension unavailable: No module named '_pcbnew'
```
* **Cause**: KiCad installs its Python C++ extension module (`_pcbnew.so`) to the system Python directory (e.g., `/usr/lib/python3/dist-packages/_pcbnew.so`). If `agent-ee` is launched from an isolated Python virtual environment without system site-packages access, Python cannot import `pcbnew`.
* **Resolution**:
  * Execute via the provided wrapper script: `./bin/agent-ee`.
  * Alternatively, create your virtual environment with system access: `python3 -m venv --system-site-packages .venv`.

### Problem: `another writer owns the build`
```
ValueError: another writer owns the build
```
* **Cause**: An active or crashed process holds a lock on `<run-dir>/.lock`.
* **Resolution**:
  1. Confirm that no other `agent-ee` process is currently executing on that run directory (`ps aux | grep agent-ee`).
  2. If the previous process was terminated abruptly (e.g., `kill -9`), delete `<run-dir>/.lock` and re-run with `--resume`.

---

## 4. Frequently Asked Questions (FAQ)

### Q1: Can agent-ee run completely headlessly without an X11 or Wayland display server?
**Yes.** All core EDA operations are executed via native command-line binaries (`kicad-cli`) and the native Python C++ extension (`_pcbnew`). 3D raytracing in `agent-ee render3d` also runs completely headlessly without requiring a display server or virtual framebuffer (Xvfb).

### Q2: Do I need LLM API keys (Anthropic, OpenAI, etc.) to generate boards?
**No.** `agent-ee build run` and `agent-ee place run` are 100% deterministic, native computational pipelines. They execute native KiCad binaries, the Rust placement solver, and `kicad-route` without making network calls or querying language models. LLM API keys are only required if you use `agent-ee design generate` or enable LLM reviewer harnesses in `agent-ee build review --profile`.

### Q3: Why does `agent-ee doctor` report an error for pcbnew when Python is installed?
KiCad's Python bindings are compiled as a native shared library (`_pcbnew.so`) bound to the system Python version. Verify that `/usr/bin/python3` can import `pcbnew`:
```bash
/usr/bin/python3 -c "import pcbnew; print(pcbnew.Version())"
```
Ensure you are running `agent-ee` with the matching system Python interpreter.

### Q4: How do I export or share completed board designs for manufacturing?
All manufacturing deliverables are collected in the run directory under:
```
<run-dir>/package/candidate/
```
This folder contains:
* RS-274X Gerber files (`*.gbr`) and NC drill files (`*.drl`).
* Pick-and-Place component positioning (`CPL.csv`).
* Bill of Materials (`BOM.csv`).
* Native KiCad design files (`*.kicad_sch`, `*.kicad_pcb`).

You can zip this directory and upload it directly to PCB manufacturers (e.g., JLCPCB). To share an interactive 3D model with stakeholders, run:
```bash
./bin/agent-ee view3d <run-dir>/package/candidate/board.kicad_pcb --out export-viewer --export-only
```

### Q5: How do I resume a build that was cancelled or stopped at a checkpoint?
Run the exact same `build run` command with the `--resume` flag:
```bash
./bin/agent-ee build run <plan> --out <run-dir> --resume
```
`agent-ee` verifies existing stage receipts and hashes, skips already completed stages, and resumes execution from the first pending stage.

---

## 5. Related Documentation

* [Quickstart Guide](quickstart.md): 5-minute walk-through for first-time builds.
* [Operator Runbook](operator-runbook.md): Production operations, checkpoints, and resource limits.
* [Architecture & Pipeline Guide](architecture-pipeline.md): Comprehensive guide to the 7-stage generative synthesis pipeline.
* [Benchmarking Guide](benchmarking-guide.md): Evaluating placement engine and dual-gate benchmark performance.
* [CLI Reference](cli-reference.md): Index of CLI commands and benchmark flags.
