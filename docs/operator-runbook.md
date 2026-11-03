# Operator Runbook: Running agent-ee in Production

This runbook provides production operations procedures for deploying, executing, monitoring, checkpointing, and verifying generative PCB builds with `agent-ee`.

---

## 1. System Readiness and Environment Verification

Production builds require a deterministic, pinned native EDA toolchain. Before initiating builds or scheduling automated pipelines, verify host readiness using the built-in diagnostic probe:

```bash
./bin/agent-ee doctor --checks-only
```

`agent-ee doctor` unconditionally outputs formatted JSON on stdout by default without requiring extra flags, making it directly ingestible by automated CI/CD pipelines and monitoring dashboards.

### Required Toolchain Dependencies

| Binary / Module | Minimum Version | Default Host Location | Verification Command |
|---|---|---|---|
| `kicad-cli` | 10.0.7 | `/usr/bin/kicad-cli` | `kicad-cli --version` |
| `_pcbnew.so` | 10.0.7 | `/usr/lib/python3/dist-packages/_pcbnew.so` | `python3 -c "import pcbnew; print(pcbnew.Version())"` |
| `agent-ee-placement` | 0.1.0 | `/usr/local/libexec/agent-ee/agent-ee-placement` | `agent-ee-placement --version` |
| `ngspice` | 45.2 | `/usr/bin/ngspice` | `ngspice --version` |
| `bwrap` | 0.11.1 | `/usr/bin/bwrap` | `bwrap --version` |
| `pdftotext` | 26.01.0 | `/usr/bin/pdftotext` | `pdftotext -v` |

### Environment Variables

| Variable | Default Value | Operational Purpose |
|---|---|---|
| `AGENT_EE_RUST_PLANNER` | `/usr/local/libexec/agent-ee/agent-ee-placement` | Explicit override path for the compiled Rust placement engine executable. |
| `AGENT_EE_KICAD_ROUTE_CHECKOUT` | `/opt/kicad-route` | Pinned repository checkout for the `kicad-route` autorouting engine. |
| `PCBSCHEMAGEN_ROOT` | `~/git/PCBSchemaGen_v2` | Clean pinned external verifier checkout; see [benchmark prerequisites](benchmarking-guide.md#executing-pcbschemagen-benchmarks). |
| `AGENT_EE_SKIP_VISUALS` | `0` | Set to `1` in headless CI jobs where 2D/3D visual snapshot generation is omitted to save time. |
| `PYTHONPATH` | None | Must include `src` when running uninstalled from a repository checkout. |

---

## 2. Standard Build Execution Procedures

### Full Production Build

To run the complete 7-stage manufacturing build pipeline:

```bash
./bin/agent-ee build run bundles/telemetry-pod/build-plan.json --out runs/telemetry-pod-v1
```

The process acquires a non-blocking shared file lock (`.lock`) inside the run directory to prevent concurrent writers from modifying in-flight builds.

### Checkpointed Execution (`--through`)

For complex or unfamiliar designs, operators can execute the pipeline up to an intermediate gate using `--through`. This halts execution after the specified stage for manual review or electrical inspection:

```bash
./bin/agent-ee build run bundles/telemetry-pod/build-plan.json --out runs/telemetry-pod-v1 --through schematic
```

Valid `--through` stage targets:
* `requirements`: Stops after verifying requirement mappings and compiling check baselines.
* `parts`: Stops after compiling the component manifest and generating `build-generated/connectivity-intent.json`.
* `scaffold`: Stops after generating the native `.kicad_pcb` envelope, stackup, and clearance rules.
* `schematic`: Stops after emitting the hierarchical `.kicad_sch` schematic and passing Electrical Rules Check (ERC).
* `placement-routing`: Stops after running the Rust geometric placement engine and completing PCB track routing.
* `artwork`: Stops after resolving silkscreen text collisions and adjusting component labels.
* `package`: Executes the complete pipeline through final DFM checks and manufacturing export (default).

> **Important**: A run stopped with `--through` records its status as `checkpoint`. Checkpoint runs never generate an accepted manufacturing package.

### Reviewing Completed Candidates (`build review`)

To execute fresh native checks and optional isolated LLM reviewer harnesses on a completed candidate without adopting a new candidate or altering original run receipts:

```bash
./bin/agent-ee build review runs/telemetry-pod-v1 --out reviews/telemetry-pod-rev1 --max-seconds 1200
```

With an explicit evaluation profile (`agent-ee.profile/1`):

```bash
./bin/agent-ee build review runs/telemetry-pod-v1 --out reviews/telemetry-pod-rev1 --profile profiles/standard.json
```

---

## 3. Resumption, Cancellation, and Signal Handling

### Controlled Early Stop via CANCEL File

To request a graceful, cooperative termination of a long-running build without killing active native child processes mid-write:

```bash
touch runs/telemetry-pod-v1/CANCEL
```

The coordinator monitors this cancel trigger file at stage and step boundaries. When detected, the coordinator raises a KeyboardInterrupt, records status `interrupted` (exit code `130`) in `build-report.json`, writes all partial stage receipts to disk, and exits cleanly.

### Signal Handling (SIGINT / Ctrl+C)

When the operator issues a `SIGINT` (Ctrl+C):
1. The coordinator catches the signal via a held interrupt handler.
2. Active sandboxed subprocesses in the process group receive `SIGTERM` (with a configurable grace period before `SIGKILL`).
3. Partial stage receipts and logs are flushed to disk.
4. The run status is marked as `interrupted` (exit code `130`).
5. No partial files in stage candidate directories (e.g. `stages/07-package-*/candidate/`) are adopted, ensuring candidate immutability is preserved.

### Resuming Interrupted or Checkpointed Builds (`--resume`)

To resume an interrupted, cancelled, or checkpointed build from where it left off:

```bash
./bin/agent-ee build run bundles/telemetry-pod/build-plan.json --out runs/telemetry-pod-v1 --resume
```

The resume logic:
1. Verifies that the build plan and input source file digests match the original run record.
2. Reads existing stage receipts from `<run>/receipts/`.
3. Verifies that output artifact hashes match previous records.
4. Automatically skips already completed stages.
5. Continues execution from the first incomplete stage.

---

## 4. Resource Allocation and Performance Tuning

### Placement Engine Tuning

Placement search budgets are defined in the build plan JSON under the `placement` block:

```json
{
  "placement": {
    "budget_s": 900,
    "search": {
      "seeds": [1, 2, 3, 4],
      "steps_per_seed": 150000,
      "shortlist": 2
    },
    "router": {
      "checkout": "/opt/kicad-route"
    }
  }
}
```

* **`budget_s`**: Total wall-clock time limit in seconds allocated to the placement solver. If exceeded, the solver returns the best candidate found within budget.
* **`steps_per_seed`**: Number of simulated annealing iterations per random seed. Increase for dense, multi-layer layouts (e.g., 200,000); decrease for quick iteration (e.g., 50,000).
* **`seeds`**: List of random seed integers. Each seed explores a different stochastic search path.
* **`shortlist`**: Number of top-ranked placement candidates forwarded to the router.

### Concurrency Tuning

* **3D Raytracing (`render3d`)**: Raytracing supports parallel rendering across camera angles:
  ```bash
  ./bin/agent-ee render3d board.kicad_pcb --out renders/ --quality high --jobs 3
  ```
  Setting `--jobs 3` renders top, bottom, and isometric views concurrently.

### Storage and Scratch Volumes

During native execution, Bubblewrap mounts a scratch directory to `/work` inside the sandbox:
* Ensure at least 5 GB of free disk space is available on the filesystem hosting `--out`.
* The coordinator mounts `/work` as a private writable bind mount and enforces `--clearenv` to guarantee no state leaks between stages.

---

## 5. Containerized Deployment

`agent-ee` can be deployed inside containerized environments (Docker or Podman) for automated CI/CD pipelines.

### Podman (Recommended for Rootless Execution)

```bash
podman run --rm -it \
  --network none \
  --security-opt label=disable \
  -v /var/cad/bundles:/bundles:ro \
  -v /var/cad/runs:/runs:rw \
  agent-ee:latest \
  build run /bundles/i2c/build-plan.json --out /runs/i2c-run
```

### Docker Unprivileged Namespace Flags

Because `agent-ee` internally uses Bubblewrap (`bwrap`) to sandbox KiCad and native tools, the outer container must grant permission to create unprivileged user namespaces:

```bash
docker run --rm -it \
  --network none \
  --cap-add SYS_ADMIN \
  --security-opt seccomp=unconfined \
  -v /var/cad/bundles:/bundles:ro \
  -v /var/cad/runs:/runs:rw \
  agent-ee:latest \
  build run /bundles/i2c/build-plan.json --out /runs/i2c-run
```

Key container constraints:
* **`--network none`**: Build execution is strictly deterministic and networkless. No network access is required or permitted during `build run`.
* **Volume Mounts**: Input bundles should be mounted read-only (`:ro`); output directories must be mounted writable (`:rw`).

---

## 6. Output Custody, Verification, and Delivery

### Cryptographic Run Verification (`agent-ee verify`)

To confirm that an executed build has not been modified or corrupted since completion:

```bash
./bin/agent-ee verify runs/telemetry-pod-v1
```

`agent-ee verify` recomputes the SHA-256 digest of every candidate file, compares them against `candidate.json`, verifies stage receipts, and checks tool runtime fingerprints. It exits with code `0` on success and code `2` if any discrepancy or file alteration is detected.

To verify candidate snapshot files without checking the original working source files:

```bash
./bin/agent-ee verify runs/telemetry-pod-v1 --snapshot-only
```

### Validating the Sealed Package

Captured inputs from preflight reside in `<run-dir>/source/`. Candidate artifacts generated at each stage reside in stage-isolated directories under `<run-dir>/stages/<index>-<stage>-<hex>/candidate/`. Operators can locate the exact active candidate directory by inspecting `<run-dir>/build-report.json` under the `"candidate"` key.

The accepted manufacturing package resides at:
```
<run-dir>/stages/07-package-<hex>/candidate/
```

Verify that `<run-dir>/stages/07-package-<hex>/accepted-package.json` contains valid SHA-256 digests matching the exported files:
* `*.gbr`: Gerber layer plots.
* `*.drl`: Excellon NC drill files.
* `BOM.csv`: Bill of Materials.
* `CPL.csv`: Pick-and-Place component placement coordinates.

### Placement and Routing Metrics (`agent-ee place metrics`)

To inspect physical placement metrics on the generated board:

```bash
./bin/agent-ee place metrics runs/telemetry-pod-v1/stages/07-package-*/candidate/board.kicad_pcb \
  --groups runs/telemetry-pod-v1/source/groups.json \
  --intent runs/telemetry-pod-v1/source/placement-intent.json
```

This outputs a structured JSON report detailing:
* Bounding box dimensions and board area.
* Total wirelength estimate.
* Electrical locality score.
* Fixed connector edge alignment verification.

---

## 7. Operational References

* [Quickstart Guide](quickstart.md): 5-minute walk-through for first-time builds.
* [Architecture & Pipeline Guide](architecture-pipeline.md): Comprehensive guide to the 7-stage generative synthesis pipeline.
* [Benchmarking Guide](benchmarking-guide.md): Evaluating emitter, placement engine, and dual-gate benchmark performance.
* [Generator Troubleshooting & FAQ](troubleshooting-faq.md): Solutions for stage failures, ERC/DRC violations, and operational questions.
* [CLI Reference](cli-reference.md): Complete index of all `agent-ee` subcommands and options.
