# Benchmarking Guide: Performance, Convergence, and Quality

This guide details the benchmarking suites and measurement procedures in `agent-ee`. Benchmarks quantify algorithmic execution speed, placement solver convergence, schematic emission latency, and physical circuit correctness across decoupled verification gates.

---

## 1. Overview of agent-ee Benchmarks

`agent-ee` incorporates four specialized benchmark suites targeting different layers of the pipeline:

1. **Emitter Benchmark (`scripts/benchmark_emitter.py`)**: Evaluates schematic emission speed, memory footprint, and KiCad XML netlist parity on complex multi-IC telemetry circuits.
2. **Placement Engine Benchmarks (`crates/placement-engine` and `scripts/seeded_benchmark.py`)**: Profiles the standalone Rust geometric placement optimizer across simulated annealing search budgets and multi-seed convergence.
3. **Design Generation Benchmarks (`agent-ee design benchmark`)**: Evaluates the LLM-driven translation loop from natural-language engineering requirements to schema-compliant `agent-ee.design/1` statements.
4. **PCBSchemaGen Dual-Gate Benchmark Suite (`agent_ee.pcbschemagen_benchmark`)**: Evaluates electronic circuit designs through two independent validation gates: Gate 1 (deterministic zero-LLM pin and net topology verification) and Gate 2 (physical board compilation, routing, and DRC).

---

## 2. Emitter Benchmark

### Purpose
The emitter benchmark measures the time required to resolve captured CAD symbol libraries, build the connectivity intermediate representation (IR), emit native KiCad 10 hierarchical schematics (`.kicad_sch`), and verify netlist parity via `kicad-cli sch export netlist`.

### Workload
The workload consists of a realistic pod-scale telemetry circuit featuring seven primary integrated circuits:
* `U1`: Espressif ESP32-S3-WROOM-1 microcontroller.
* `U2`: NXP TJA1051T-3 CAN-FD transceiver.
* `U3`: STMicroelectronics LSM6DSM 6-axis IMU.
* `U4`: u-blox NEO-M8N GPS/GNSS receiver.
* `U5`: Microchip MCP3204 4-channel 12-bit ADC.
* `U6`: TI TPS5430DDA step-down switching regulator.
* `U7`: TI TPS62933 step-down switching regulator.
* Passives, decoupling arrays, and a 40-pin expansion header (`Conn_01x40`), totaling over 130 pins and nets.

### Execution
Run the benchmark script from the repository root:

```bash
PYTHONPATH=src python3 scripts/benchmark_emitter.py --out /tmp/emitter-benchmark
```

### Measured Metrics
* **Symbol Resolution Time**: Time taken to parse captured KiCad `.kicad_sym` libraries and resolve symbol pin tables.
* **Schematic Emission Time**: Time taken to write hierarchical `.kicad_sch` S-expression files.
* **Netlist Parity**: Validates that the native KiCad netlist generated from the emitted schematic matches the connectivity IR without missing or extraneous connections.

---

## 3. Placement Engine Benchmarks

### Rust Unit & Integration Tests
The Rust placement engine (`agent-ee-placement`) implements pure geometric planning, broad-phase bounding box rejection, exact polygon geometry, and simulated annealing.

To run the unit test and regression suite:

```bash
cargo test --manifest-path crates/placement-engine/Cargo.toml
```

The test suite compares placement results and refusal diagnostics against the Python reference planner to ensure numerical parity.

### Seeded Synthetic Benchmarks (`scripts/seeded_benchmark.py`)
The seeded benchmark runner evaluates placement solver convergence across multiple random seeds and problem instances.

Common subcommands:
* `fetch`: Retrieves benchmark board manifests and archives.
* `prepare`: Generates placement problems and geometry files.
* `run`: Executes placement search budgets across random seeds.
* `report`: Compiles structured JSON reports comparing solver outcomes.

Example invocation:

```bash
python3 scripts/seeded_benchmark.py prepare benchmarks/seeded/expectations.json /tmp/bench-cache /tmp/bench-prepared
```

### Measured Placement Metrics
* **Iterations per Second**: Number of simulated annealing moves evaluated per second.
* **Thermal Cooling Convergence**: Rate of cost function reduction over the annealing schedule.
* **Objective Cost Breakdown**:
  * Wirelength estimate (half-perimeter bounding box).
  * Functional group clustering.
  * Electrical locality score.
  * Fixed connector edge alignment penalties.
  * Hard overlap penalties (component bodies and pad clearances).
  * Differential pair escape-space clearance.

---

## 4. Design Generation Benchmarks

### Purpose
The design generation benchmark evaluates the autonomous translation of natural-language engineering requirements (`REQUIREMENTS.md`) into valid, manufacturing-ready `agent-ee.design/1` specifications.

### Execution
Run `agent-ee design benchmark` pointing to a directory containing requirements markdown files:

```bash
./bin/agent-ee design benchmark fixtures/design-requirements/ \
  --out runs/bench-requirements \
  --author codex:gpt-6.1-sol:high \
  --max-attempts 4 \
  --max-seconds 2400
```

To run generation against a single requirements document (such as `fixtures/design-requirements/current-monitor.md`), use `agent-ee design generate`:

```bash
./bin/agent-ee design generate fixtures/design-requirements/current-monitor.md \
  --out runs/current-monitor-gen \
  --author codex:gpt-6.1-sol:high \
  --max-attempts 2 \
  --max-seconds 2400
```

### Measured Metrics
* **Synthesis Success Rate**: Proportion of attempts yielding a valid, schema-compliant design statement.
* **Trace Citation Parity**: Checks that all functional assertions in `agent-ee.design/1` cite explicit requirement paragraphs in the source Markdown.
* **Time to First Valid Design**: Wall-clock seconds spent querying the LLM author before producing a valid statement.

---

## 5. PCBSchemaGen Dual-Gate Benchmark Suite

The PCBSchemaGen benchmark suite evaluates circuit candidates across 25 discrete-IC tasks defined in `fixtures/pcbschemagen-tier1/manifest.json` (18 tasks from `pcbbench`, 7 tasks from `open_schematics`).

### Decoupled Dual-Gate Architecture

```
                  +-----------------------------------------+
                  |            Candidate Circuit            |
                  +--------------------+--------------------+
                                       |
                   +-------------------+-------------------+
                   |                                       |
                   v                                       v
      +-------------------------+             +-------------------------+
      |  Gate 1: Topo Verifier  |             |  Gate 2: Physical Build |
      |   (Knowledge Graph KG)  |             |   (KiCad DRC & Routing) |
      +------------+------------+             +------------+------------+
                   |                                       |
                   +-------------------+-------------------+
                                       |
                                       v
                        +------------------------------+
                        |     Divergence Analysis      |
                        |   4 Classification States    |
                        +------------------------------+
```

* **Gate 1: Topo Verifier (`agent_ee.pcbschemagen_gate1`)**:
  * Executes deterministic, zero-LLM pin role and net topology matching against the PCBSchemaGen Knowledge Graph (`Gate1KGStore`).
  * Filters authorized 2.54 mm external headers so interface connectors do not trigger false component count or connectivity errors.
  * Emits report schema `agent-ee.pcbschemagen-gate1/1`.
* **Gate 2: Physical Board Compiler (`agent_ee.design_compile` & `build_workflow`)**:
  * Compiles the design into physical board artifacts, places components, executes routing, and runs native KiCad ERC and DRC.
  * Confirms physical manufacturability (0 open tracks, 0 DRC violations, layer count within limit, edge connectors on perimeter).

### The Four Divergence Quadrants

The dual-gate runner scores each task as a 2-tuple `(Gate 1: PASS/FAIL, Gate 2: PASS/FAIL)` and classifies it into one of four divergence quadrants:

| Quadrant | Gate 1 (Topo) | Gate 2 (Physical) | Architectural Meaning |
|---|---|---|---|
| `concordant_pass` | **PASS** | **PASS** | **Complete Success**: The circuit is semantically correct according to the Knowledge Graph and physically manufacturable with clean DRC. |
| `concordant_fail` | **FAIL** | **FAIL** | **Complete Failure**: The circuit has invalid electrical connections and cannot be compiled or routed physically. |
| `physical_bottleneck` | **PASS** | **FAIL** | **Physical Failure**: The schematic is semantically correct, but the physical board fails clearance, routing completion, layer limits, or DRC. |
| `semantic_bypass` | **FAIL** | **PASS** | **Semantic Failure**: The board physically routes and passes DRC, but the circuit is electrically incorrect (wrong pin connections, missing passives). |

### Executing PCBSchemaGen Benchmarks

Gate 1 requires a separate, clean Git checkout of
[`HZou9/PCBSchemaGen_v2`](https://github.com/HZou9/PCBSchemaGen_v2). Use the
current upstream `main`; the adapter accepts any commit and records the one it
used. The verifier code and knowledge graphs are external inputs; agent-ee does
not redistribute them. Provision them before running either benchmark CLI, and
update them the same way later:

```bash
git clone https://github.com/HZou9/PCBSchemaGen_v2 "$HOME/git/PCBSchemaGen_v2"
export PCBSCHEMAGEN_ROOT="$HOME/git/PCBSchemaGen_v2"
# Later, to move an existing checkout (also a detached one) to the current main:
git -C "$PCBSCHEMAGEN_ROOT" checkout main
git -C "$PCBSCHEMAGEN_ROOT" pull --ff-only
```

Install that checkout's `requirements.txt` in the Python environment used for
the benchmark (`networkx>=3.0` and `pyyaml>=6.0`). Both CLIs also accept
`--pcbschemagen-root DIR`; the default is `$PCBSCHEMAGEN_ROOT` or
`~/git/PCBSchemaGen_v2`.

Before each Gate 1 run the adapter reads the checkout's commit and requires:

- a directory that exists (otherwise the verifier input is missing);
- the top of its own Git checkout, not a plain directory and not a directory
  inside another repository, whose commit would not identify the verifier;
- no changes to tracked files, staged or not. Untracked files, such as import
  bytecode, are allowed;
- the verifier files `framework/topo/kg_loader.py`, `framework/task_config.yaml`,
  `component.json`, `kg_component.json` and `kg/kg_open_schematics.json`, and the
  Python packages `yaml` and `networkx`.

Gate 1 does not run the checkout itself. Once per process it exports the
accepted commit with `git archive` into a private temporary directory, imports
`framework.topo` from there and reads the knowledge graphs from there. Gate 1
reports record `verifier: {repository, commit}`: `repository` names the
upstream project and `commit` is that accepted commit, so the record identifies
every verifier input of the run even if the checkout changes while it runs.
The dual-gate benchmark keeps that record in each task's `gate1` result. It is
`null` for a missing candidate or a Gate 1 execution error, which produce no
verifier result. Gate 1 refuses to run in a process that already imported a
module of the verifier's `framework` package from anywhere but its copy, and
refuses a result if any `framework` module loaded during the run came from
outside the copy (for example an untracked module found only in the checkout).
Imported verifier code stays loaded in a process, so a process that imported the
code at one commit refuses a checkout at another and asks for a new process. The adapter never fetches a checkout or falls back to another
one. A missing or invalid verifier produces a CLI input error (exit 2),
without classifying the circuit as a topology failure or a semantic bypass.
Results from different verifier commits are not directly comparable, because
an upstream knowledge-graph change can change a verdict; compare the recorded
commits first.

Translation, XML parsing and dependency-refusal tests use owned small fixtures.
The real-verifier tests in `test_pcbschemagen_gate1` and
`test_pcbschemagen_benchmark` require the external input. Without it, strict
mode reports the `pcbschemagen-verifier` partition as **not executed** and
fails the suite; it cannot report a pass. See
[required external inputs](container.md#required-external-inputs) for container
mounting and strict-suite reporting.

#### Evaluating a Single Task Candidate
To evaluate a candidate design against Task `104` (USB_DATA_LOGGER):

Note: `--candidate` requires a candidate design JSON file (e.g. `evaluation_candidates/104.json`), whereas `fixtures/pcbschemagen-tier1/tasks/104.json` contains the benchmark task specification.

```bash
PYTHONPATH=src python3 -m agent_ee.pcbschemagen_benchmark \
  --task-id 104 \
  --candidate evaluation_candidates/104.json \
  --summary
```

#### Evaluating a Full Candidate Directory
To evaluate all 25 benchmark tasks across a directory of candidate designs:

```bash
PYTHONPATH=src python3 -m agent_ee.pcbschemagen_benchmark \
  --manifest fixtures/pcbschemagen-tier1/manifest.json \
  --candidates-dir evaluation_candidates/ \
  --out benchmark_results.json \
  --summary
```

#### Standalone Gate 1 Topo Verification
To run only the deterministic schematic verifier:

```bash
PYTHONPATH=src python3 -m agent_ee.pcbschemagen_gate1 \
  --design candidate_design.json \
  --task-id 104 \
  --out gate1_report.json
```

---

## 6. Metrics, Thresholds, and Baseline Interpretation

### Acceptance Thresholds
* **Gate 1 Pass**: Requires `reward == 1.0`, `critical_errors == []`, and `error_count == 0`.
* **Gate 2 Pass**: Requires `passed == True`, `erc_errors == 0`, `drc_errors == 0`, and `unconnected_items == 0`.
* **Dual-Gate Pass**: Both Gate 1 and Gate 2 must report `PASS` (`concordant_pass`).

### Sample Dual-Gate Summary Output
When invoked with `--summary`, `pcbschemagen_benchmark` prints an operator summary table:

```
================================================================================
PCBSchemaGen Tier-1 Dual-Gate Benchmark Summary
================================================================================
Total Tasks Evaluated: 25
Gate 1 Passed:         23 / 25 (92.0%)
Gate 2 Passed:         22 / 25 (88.0%)
Dual-Gate Passed:      21 / 25 (84.0%)

Divergence Quadrant Breakdown:
  concordant_pass:     21 (84.0%) [G1: PASS, G2: PASS]
  concordant_fail:      1 ( 4.0%) [G1: FAIL, G2: FAIL]
  physical_bottleneck:  2 ( 8.0%) [G1: PASS, G2: FAIL]
  semantic_bypass:      1 ( 4.0%) [G1: FAIL, G2: PASS]
================================================================================
```

---

## 7. Related Documentation

* [Quickstart Guide](quickstart.md): Running your first PCB generation build.
* [Operator Runbook](operator-runbook.md): Production run execution, checkpoints, and resource limits.
* [Architecture & Pipeline Guide](architecture-pipeline.md): Comprehensive guide to the 7-stage generative synthesis pipeline.
* [Generator Troubleshooting & FAQ](troubleshooting-faq.md): Diagnosing pipeline errors and benchmark divergence.
* [CLI Reference](cli-reference.md): Index of CLI commands and benchmark flags.
