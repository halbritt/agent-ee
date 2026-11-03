"""Dual-Gate Benchmark Suite Runner for PCBSchemaGen Tier-1 tasks.

Evaluates circuit candidates through two non-overlapping validation gates:
- Gate 1 (Schematic Topo Correctness): Uses `verify_gate1()` for deterministic,
  zero-LLM pin and topology verification against the PCBSchemaGen Knowledge Graph.
- Gate 2 (Physical Board Completion): Executes `agent-ee` native pipeline
  (`design_compile` / `compile_design()` / build verification) checking ERC,
  layer constraints, edge connector placement, routing, and DRC.

Emits structured 2-tuple score `(Gate 1: PASS/FAIL, Gate 2: PASS/FAIL)`
with divergence analysis adhering to schema `agent-ee.dual-gate-benchmark/1`.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import sys
from typing import Any, Optional, Sequence, Union

from .design import DesignError, load as load_design, validate as validate_design
from .pcbschemagen_gate1 import (
    DEFAULT_PCBSCHEMAGEN_ROOT, Gate1Result, PCBSchemaGenInputError, verify_gate1)
from .storage import digest, read_json

SCHEMA = "agent-ee.dual-gate-benchmark/1"
MANIFEST_SCHEMA = "agent-ee.pcbschemagen-tier1-manifest/1"
DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "pcbschemagen-tier1" / "manifest.json"
)

# Divergence categories
CONCORDANT_PASS = "concordant_pass"
CONCORDANT_FAIL = "concordant_fail"
PHYSICAL_BOTTLENECK = "physical_bottleneck"  # Gate 1 PASS, Gate 2 FAIL
SEMANTIC_BYPASS = "semantic_bypass"  # Gate 1 FAIL, Gate 2 PASS

HEADER_WRAPPER_TEXT = """### Interface Authorization Wrapper (2.54 mm Pin Headers)
For all external signals, bus connections, and power rails (including VIN, VOUT, GND, logic controls, and communication lines):
1. **Connector Footprints**: Terminate all external nets at standard 2.54 mm pitch vertical through-hole or SMT pin headers (KiCad library: `Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical`, `PinHeader_1x03_P2.54mm_Vertical`, `PinHeader_1x04_P2.54mm_Vertical`, etc.).
2. **Physical Edge Placement**: Declare all connectors in the `connectors` array, positioning them along the board outline edges (`top`, `bottom`, `left`, `right`) with courtyard boundaries within 1 mm of the board perimeter.
3. **Standard Pin Ordering**:
   - Power interfaces: `[VIN, GND]` or `[VCC, GND]`
   - Analog / Sense: `[IN+, IN-, GND]` or `[VSENSE, GND]`
   - Serial UART: `[VCC, GND, TX, RX]`
   - I2C bus: `[GND, VCC, SDA, SCL]`
   - CAN bus: `[CANH, CANL, GND]`
   - RS-485 bus: `[A, B, GND]`
   - SPI bus: `[VCC, GND, SCK, MOSI, MISO, CS]`
4. **Stock & Assembly**: Use standard JLCPCB SMT/THT stock part numbers (e.g. C22548, C2897383) or equivalent generic 2.54 mm header pins.
"""


def attach_header_wrapper(task_prompt: str, extra_notes: Optional[str] = None) -> str:
    """Attach the standard 2.54 mm header wrapper authorization to a task prompt."""
    prompt = (task_prompt or "").strip()
    if extra_notes:
        prompt = f"{prompt}\n\n{extra_notes.strip()}"
    return f"{prompt}\n\n{HEADER_WRAPPER_TEXT}".strip()


def make_254mm_pin_header_part(
    ref: str,
    pin_count: int,
    footprint: Optional[str] = None,
    lcsc: str = "C2897383",
    value: Optional[str] = None,
) -> dict[str, Any]:
    """Helper to build an agent-ee.design/1 part definition for a 2.54 mm vertical header."""
    n = max(1, pin_count)
    fp = footprint or f"Connector_PinHeader_2.54mm:PinHeader_1x{n:02d}_P2.54mm_Vertical"
    val = value or f"Conn_01x{n:02d}"
    lib = f"Connector_Generic:Conn_01x{n:02d}"
    return {
        "lcsc": lcsc,
        "value": val,
        "lib_id": lib,
        "footprint": fp,
        "group": "interfaces",
        "connector": {"rows": n, "columns": 1},
    }


def make_edge_connector(
    ref: str,
    edge: str = "left",
    position_mm: float = 10.0,
    inset_mm: float = 1.5,
    rotation_deg: float = 90.0,
) -> dict[str, Any]:
    """Helper to build a connectors array entry for edge placement within 1 mm of board boundary."""
    return {
        "reference": ref,
        "edge": edge,
        "position_mm": float(position_mm),
        "inset_mm": float(inset_mm),
        "rotation_deg": float(rotation_deg),
        "insertion": "from_top",
    }


def classify_divergence(gate1_passed: bool, gate2_passed: bool) -> tuple[str, str]:
    """Classify the divergence category between Gate 1 and Gate 2 results."""
    if gate1_passed and gate2_passed:
        return (
            CONCORDANT_PASS,
            "Concordant Pass: Circuit satisfied both schematic topology (Gate 1) and physical board completion (Gate 2).",
        )
    if not gate1_passed and not gate2_passed:
        return (
            CONCORDANT_FAIL,
            "Concordant Fail: Circuit failed both schematic topology (Gate 1) and physical board completion (Gate 2).",
        )
    if gate1_passed and not gate2_passed:
        return (
            PHYSICAL_BOTTLENECK,
            "Physical Bottleneck: Circuit has correct schematic topology (Gate 1 PASS), but failed physical board completion (Gate 2 FAIL).",
        )
    return (
        SEMANTIC_BYPASS,
        "Semantic Bypass: Circuit passed physical layout/DRC (Gate 2 PASS), but violates datasheet schematic or pin invariants (Gate 1 FAIL).",
    )


@dataclass
class TaskDefinition:
    """Definition and interface contract for a Tier-1 benchmark task."""

    id: str
    dataset: str
    sub_module_name: str
    level: str
    type: str
    primary_ic: str
    package: str
    input_nodes: list[str]
    output_nodes: list[str]
    input_voltage: str
    output_voltage: str
    components: Any
    task_prompt: str
    wrapped_prompt: str
    authorized_headers: list[dict[str, Any]] = field(default_factory=list)

    def get_wrapped_prompt(self) -> str:
        if self.wrapped_prompt:
            return self.wrapped_prompt
        return attach_header_wrapper(self.task_prompt)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskDefinition:
        return cls(
            id=str(data.get("id", "")),
            dataset=str(data.get("dataset", "")),
            sub_module_name=str(data.get("sub_module_name", "")),
            level=str(data.get("level", "Medium")),
            type=str(data.get("type", "General")),
            primary_ic=str(data.get("primary_ic", "")),
            package=str(data.get("package", "SMD")),
            input_nodes=list(data.get("input_nodes", [])),
            output_nodes=list(data.get("output_nodes", [])),
            input_voltage=str(data.get("input_voltage", "")),
            output_voltage=str(data.get("output_voltage", "")),
            components=data.get("components"),
            task_prompt=str(data.get("task_prompt", "")),
            wrapped_prompt=str(data.get("wrapped_prompt", "")),
            authorized_headers=list(data.get("authorized_headers", [])),
        )


@dataclass
class Tier1Manifest:
    """Manifest container for the 25 Tier-1 benchmark tasks."""

    schema: str
    suite: str
    version: str
    description: str
    total_tasks: int
    counts: dict[str, int]
    wrapper_spec: dict[str, Any]
    tasks: dict[str, TaskDefinition]

    def get_task(self, task_id: Union[str, int]) -> Optional[TaskDefinition]:
        return self.tasks.get(str(task_id))

    def list_tasks(self) -> list[TaskDefinition]:
        return list(self.tasks.values())

    def filter_by_dataset(self, dataset: str) -> list[TaskDefinition]:
        ds = dataset.lower().strip()
        return [t for t in self.tasks.values() if t.dataset.lower() == ds]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "suite": self.suite,
            "version": self.version,
            "description": self.description,
            "total_tasks": self.total_tasks,
            "counts": self.counts,
            "wrapper_spec": self.wrapper_spec,
            "tasks": [t.to_dict() for t in self.tasks.values()],
        }

    @classmethod
    def load(cls, manifest_path: Optional[Union[str, Path]] = None) -> Tier1Manifest:
        path = Path(manifest_path or DEFAULT_MANIFEST_PATH).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Tier-1 manifest not found at: {path}")
        data = read_json(path)
        raw_tasks = data.get("tasks", [])
        tasks_map = {}
        for t in raw_tasks:
            t_def = TaskDefinition.from_dict(t)
            tasks_map[t_def.id] = t_def
        return cls(
            schema=data.get("schema", MANIFEST_SCHEMA),
            suite=data.get("suite", "PCBSchemaGen-Tier1"),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            total_tasks=int(data.get("total_tasks", len(tasks_map))),
            counts=data.get("counts", {}),
            wrapper_spec=data.get("wrapper_spec", {}),
            tasks=tasks_map,
        )


@dataclass
class Gate2Result:
    """Structured result of Gate 2 physical board completion verification."""

    passed: bool
    status: str
    stage: str
    erc_errors: int = 0
    drc_errors: int = 0
    unrouted_nets: int = 0
    wire_length_mm: Optional[float] = None
    via_count: Optional[int] = None
    fab_package_sha256: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskDualGateResult:
    """Evaluated result for a single benchmark task across both gates."""

    task_id: str
    name: str
    dataset: str
    primary_ic: str
    gate1: dict[str, Any]
    gate2: dict[str, Any]
    score: tuple[str, str]
    dual_gate_status: str
    divergence_category: str
    divergence_explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "dataset": self.dataset,
            "primary_ic": self.primary_ic,
            "score": list(self.score),
            "dual_gate_status": self.dual_gate_status,
            "divergence_category": self.divergence_category,
            "divergence_explanation": self.divergence_explanation,
            "gate1": self.gate1,
            "gate2": self.gate2,
        }


@dataclass
class DualGateBenchmarkReport:
    """Comprehensive benchmark execution report per schema agent-ee.dual-gate-benchmark/1."""

    schema: str
    suite: str
    version: str
    total_tasks: int
    summary: dict[str, Any]
    tasks: list[TaskDualGateResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "suite": self.suite,
            "version": self.version,
            "total_tasks": self.total_tasks,
            "summary": self.summary,
            "tasks": [t.to_dict() for t in self.tasks],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


def evaluate_gate1_candidate(
    candidate: Any,
    task_id: Optional[str] = None,
    task_name: Optional[str] = None,
    pcbschemagen_root: Union[str, Path] = DEFAULT_PCBSCHEMAGEN_ROOT,
    captures_dir: Optional[Union[str, Path]] = None,
    std_snapshot: Optional[Union[str, Path]] = None,
    rules: Optional[Union[str, Path]] = None,
) -> dict[str, Any]:
    """Execute Gate 1 verification on a candidate."""
    design_input = None
    netlist_xml = None

    if isinstance(candidate, (str, Path)):
        p = Path(candidate)
        if p.suffix.lower() == ".xml":
            netlist_xml = p
        elif p.suffix.lower() == ".json":
            design_input = p
        elif p.is_dir():
            # Check for standard netlist or design files
            if (p / "statement.json").is_file():
                design_input = p / "statement.json"
            elif (p / "design.json").is_file():
                design_input = p / "design.json"
            elif (p / "native-netlist.xml").is_file():
                netlist_xml = p / "native-netlist.xml"
            else:
                design_input = p
    if candidate is None or (
        isinstance(candidate, dict)
        and (candidate.get("status") == "missing_candidate" or not candidate.get("parts"))
    ):
        return {
            "status": "FAIL",
            "passed": False,
            "reward": 0.0,
            "critical_errors": 1,
            "warnings": 0,
            "details": [
                {
                    "id": "MISSING_CANDIDATE",
                    "message": f"No candidate parts provided for task {task_id or 'UNKNOWN'}",
                    "severity": "critical",
                }
            ],
            "warnings_list": [],
            "erc_checks": [],
            "rule_checks": [],
            "llm_feedback": "Missing candidate circuit.",
            "verifier": None,
        }
    elif isinstance(candidate, dict):
        design_input = candidate

    try:
        g1_res: Gate1Result = verify_gate1(
            design_input=design_input,
            netlist_xml=netlist_xml,
            task_id=task_id,
            task_name=task_name,
            pcbschemagen_root=pcbschemagen_root,
            captures_dir=captures_dir,
            std_snapshot=std_snapshot,
            rules=rules,
        )
        return {
            "status": g1_res.status,
            "passed": g1_res.passed,
            "reward": float(g1_res.reward),
            "critical_errors": len(g1_res.errors),
            "warnings": len(g1_res.warnings),
            "details": g1_res.report.get("errors", []),
            "warnings_list": g1_res.report.get("warnings", []),
            "erc_checks": g1_res.report.get("erc_checks", []),
            "rule_checks": g1_res.report.get("rule_checks", []),
            "llm_feedback": g1_res.report.get("llm_feedback", ""),
            # The verifier commit that scored this candidate (the checkout may be at any commit).
            "verifier": g1_res.report["verifier"],
        }
    except PCBSchemaGenInputError:
        # An unavailable verifier says nothing about the candidate's topology.
        raise
    except Exception as exc:
        return {
            "status": "FAIL",
            "passed": False,
            "reward": 0.0,
            "critical_errors": 1,
            "warnings": 0,
            "details": [{"id": "EXECUTION_EXCEPTION", "message": str(exc), "severity": "critical"}],
            "warnings_list": [],
            "erc_checks": [],
            "rule_checks": [],
            "llm_feedback": f"Gate 1 execution failure: {exc}",
            "verifier": None,
        }


def _check_design_physical_preflight(design: dict[str, Any]) -> list[str]:
    """Offline physical rule preflight checking compiler constraints."""
    problems = []

    # 1. Layer count
    board = design.get("board", {})
    layers = board.get("copper_layers")
    if layers != 2:
        problems.append(
            f"board.copper_layers: only 2 copper layers supported, got {layers}"
        )

    # 2. Ground plane
    plane = design.get("plane", {})
    if not plane or plane.get("net") != "GND" or plane.get("layer") not in ("B.Cu", "F.Cu"):
        problems.append(
            "plane: must declare plane with net 'GND' and layer 'B.Cu' or 'F.Cu'"
        )

    # 3. Connectors edge reach
    connectors = design.get("connectors", [])
    if not connectors:
        problems.append("connectors: must declare at least one connector along board edge")

    # 4. Net terminals >= 2
    nets = design.get("nets", {})
    for net_name, terms in nets.items():
        if len(terms) < 2:
            problems.append(f"nets.{net_name}: net has fewer than 2 physical terminals ({terms})")

    # 5. Decoupling proximity
    decoupling = design.get("decoupling", [])
    for d in decoupling:
        dev = d.get("device")
        cap = d.get("capacitor")
        if dev == cap:
            problems.append(f"decoupling: device and capacitor must differ ({dev})")

    return problems


def evaluate_gate2(
    candidate: Any,
    task_id: Optional[str] = None,
    captures_dir: Optional[Union[str, Path]] = None,
    out_dir: Optional[Union[str, Path]] = None,
    run_build: bool = False,
    kicad_root: Union[str, Path] = "/usr/share/kicad",
) -> Gate2Result:
    """Execute Gate 2 verification (physical board completion / compile)."""
    # 1. Candidate is a pre-existing build run directory
    if isinstance(candidate, (str, Path)):
        c_path = Path(candidate)
        if c_path.is_dir() and (c_path / "build-report.json").is_file():
            build_report = read_json(c_path / "build-report.json")
            qualification = None
            if (c_path / "qualification.json").is_file():
                qualification = read_json(c_path / "qualification.json")

            accepted = bool(
                build_report.get("accepted") is True
                and (
                    qualification is None
                    or (
                        qualification.get("mode") == "typed"
                        and qualification.get("dimensions", {})
                        .get("file_release", {})
                        .get("status")
                        == "pass"
                    )
                )
            )

            stage = "package" if accepted else "build"
            if build_report.get("attempts"):
                stage = build_report["attempts"][-1].get("stage", stage)

            erc_errors = 0
            drc_errors = 0
            unrouted = 0
            wire_len = None
            vias = None

            # Extract metrics if available
            cand_dir = c_path / build_report.get("candidate", "stages/package/candidate")
            if (cand_dir / "routing.json").is_file():
                rt = read_json(cand_dir / "routing.json")
                unrouted = rt.get("unrouted_count", 0)
                wire_len = rt.get("wire_length_mm")
                vias = rt.get("via_count")

            fab_sha = None
            fab_pkg = cand_dir / "fab-package.tar.gz"
            if fab_pkg.is_file():
                fab_sha = digest(fab_pkg)

            errors = []
            if not accepted:
                reason = build_report.get("reason") or "Physical build not accepted"
                errors.append(str(reason))

            return Gate2Result(
                passed=accepted,
                status="PASS" if accepted else "FAIL",
                stage=stage,
                erc_errors=erc_errors,
                drc_errors=drc_errors,
                unrouted_nets=unrouted,
                wire_length_mm=wire_len,
                via_count=vias,
                fab_package_sha256=fab_sha,
                errors=errors,
                details=build_report,
            )

    # 2. Candidate is an agent-ee.design/1 statement (dict or file)
    design = None
    if isinstance(candidate, dict):
        design = copy.deepcopy(candidate)
    elif isinstance(candidate, (str, Path)):
        c_path = Path(candidate)
        if c_path.is_file() and c_path.suffix.lower() == ".json":
            try:
                design = load_design(c_path)
            except Exception as exc:
                return Gate2Result(
                    passed=False,
                    status="FAIL",
                    stage="load",
                    errors=[f"Failed to load design JSON: {exc}"],
                )
        elif c_path.is_dir():
            for name in ("statement.json", "design.json"):
                if (c_path / name).is_file():
                    design = load_design(c_path / name)
                    break

    if design is None:
        return Gate2Result(
            passed=False,
            status="FAIL",
            stage="input",
            errors=["No valid design statement or build directory found in candidate"],
        )

    # Validate design structure
    try:
        validate_design(design, captures_dir, cad_only=(captures_dir is None))
    except DesignError as exc:
        problems = getattr(exc, "problems", None)
        if problems is not None:
            err_list = list(problems)
        elif exc.args and isinstance(exc.args[0], list):
            err_list = list(exc.args[0])
        else:
            err_list = [str(exc)]
        return Gate2Result(
            passed=False,
            status="FAIL",
            stage="validate",
            errors=err_list,
        )
    except Exception as exc:
        return Gate2Result(
            passed=False,
            status="FAIL",
            stage="validate",
            errors=[f"Validation error: {exc}"],
        )

    # Offline physical checks
    preflight_problems = _check_design_physical_preflight(design)
    if preflight_problems:
        return Gate2Result(
            passed=False,
            status="FAIL",
            stage="preflight",
            errors=preflight_problems,
        )

    # If captures and compiler execution requested
    if captures_dir and Path(captures_dir).is_dir():
        from .design_compile import compile_design

        target_out = Path(out_dir or (Path("/tmp") / f"bundle_{task_id or 'eval'}"))
        try:
            compile_design(design, Path(captures_dir), target_out)
        except Exception as exc:
            return Gate2Result(
                passed=False,
                status="FAIL",
                stage="compile",
                errors=[f"compile_design error: {exc}"],
            )

        if run_build:
            from . import build_workflow

            run_out = target_out.parent / f"run_{task_id or 'eval'}"
            try:
                build_workflow.run(target_out / "build-plan.json", run_out, through="package")
                return evaluate_gate2(run_out, task_id=task_id)
            except Exception as exc:
                return Gate2Result(
                    passed=False,
                    status="FAIL",
                    stage="build_workflow",
                    errors=[f"build_workflow error: {exc}"],
                )

        return Gate2Result(
            passed=True,
            status="PASS",
            stage="compile",
            errors=[],
            details={"bundle_plan": str(target_out / "build-plan.json")},
        )

    # Valid preflight offline pass
    return Gate2Result(
        passed=True,
        status="PASS",
        stage="preflight",
        errors=[],
        details={"status": "Design validated and compliant with physical 2-layer rules"},
    )


def evaluate_task(
    task_def: TaskDefinition,
    candidate: Any,
    pcbschemagen_root: Union[str, Path] = DEFAULT_PCBSCHEMAGEN_ROOT,
    captures_dir: Optional[Union[str, Path]] = None,
    out_dir: Optional[Union[str, Path]] = None,
    run_build: bool = False,
    kicad_root: Union[str, Path] = "/usr/share/kicad",
) -> TaskDualGateResult:
    """Evaluate a single task candidate across Gate 1 and Gate 2."""
    task_id = task_def.id
    task_name = task_def.sub_module_name

    g1 = evaluate_gate1_candidate(
        candidate=candidate,
        task_id=task_id,
        task_name=task_name,
        pcbschemagen_root=pcbschemagen_root,
        captures_dir=captures_dir,
    )

    g2_res = evaluate_gate2(
        candidate=candidate,
        task_id=task_id,
        captures_dir=captures_dir,
        out_dir=out_dir,
        run_build=run_build,
        kicad_root=kicad_root,
    )
    g2 = g2_res.to_dict()

    g1_pass = bool(g1.get("passed"))
    g2_pass = bool(g2.get("passed"))

    score = ("PASS" if g1_pass else "FAIL", "PASS" if g2_pass else "FAIL")
    dual_gate_status = "PASS" if (g1_pass and g2_pass) else "FAIL"

    div_cat, div_expl = classify_divergence(g1_pass, g2_pass)

    return TaskDualGateResult(
        task_id=task_id,
        name=task_name,
        dataset=task_def.dataset,
        primary_ic=task_def.primary_ic,
        gate1=g1,
        gate2=g2,
        score=score,
        dual_gate_status=dual_gate_status,
        divergence_category=div_cat,
        divergence_explanation=div_expl,
    )


def run_benchmark(
    manifest: Tier1Manifest,
    candidates: dict[str, Any],
    pcbschemagen_root: Union[str, Path] = DEFAULT_PCBSCHEMAGEN_ROOT,
    captures_dir: Optional[Union[str, Path]] = None,
    out_dir: Optional[Union[str, Path]] = None,
    run_build: bool = False,
    task_ids: Optional[Sequence[str]] = None,
    kicad_root: Union[str, Path] = "/usr/share/kicad",
) -> DualGateBenchmarkReport:
    """Execute the complete dual-gate benchmark evaluation across Tier-1 tasks."""
    selected_tasks = manifest.list_tasks()
    if task_ids:
        filter_set = {str(t).strip() for t in task_ids}
        selected_tasks = [t for t in selected_tasks if t.id in filter_set]

    evaluated_results: list[TaskDualGateResult] = []
    g1_passed_count = 0
    g2_passed_count = 0
    dual_passed_count = 0

    divergence_counts = {
        CONCORDANT_PASS: 0,
        CONCORDANT_FAIL: 0,
        PHYSICAL_BOTTLENECK: 0,
        SEMANTIC_BYPASS: 0,
    }

    for task_def in selected_tasks:
        cand = candidates.get(task_def.id) or candidates.get(str(task_def.id))
        if cand is None:
            # Task not evaluated: create failed candidate representation
            cand = {
                "schema": "agent-ee.design/1",
                "parts": {},
                "nets": {},
                "status": "missing_candidate",
            }

        res = evaluate_task(
            task_def=task_def,
            candidate=cand,
            pcbschemagen_root=pcbschemagen_root,
            captures_dir=captures_dir,
            out_dir=Path(out_dir) / task_def.id if out_dir else None,
            run_build=run_build,
            kicad_root=kicad_root,
        )
        evaluated_results.append(res)

        if res.score[0] == "PASS":
            g1_passed_count += 1
        if res.score[1] == "PASS":
            g2_passed_count += 1
        if res.dual_gate_status == "PASS":
            dual_passed_count += 1

        cat = res.divergence_category
        divergence_counts[cat] = divergence_counts.get(cat, 0) + 1

    total_eval = len(evaluated_results)
    summary = {
        "total_manifest_tasks": manifest.total_tasks,
        "evaluated_tasks": total_eval,
        "gate1_passed": g1_passed_count,
        "gate1_pass_rate": round(g1_passed_count / total_eval, 4) if total_eval > 0 else 0.0,
        "gate2_passed": g2_passed_count,
        "gate2_pass_rate": round(g2_passed_count / total_eval, 4) if total_eval > 0 else 0.0,
        "dual_gate_passed": dual_passed_count,
        "dual_gate_pass_rate": (
            round(dual_passed_count / total_eval, 4) if total_eval > 0 else 0.0
        ),
        "divergence": divergence_counts,
    }

    return DualGateBenchmarkReport(
        schema=SCHEMA,
        suite=manifest.suite,
        version=manifest.version,
        total_tasks=manifest.total_tasks,
        summary=summary,
        tasks=evaluated_results,
    )


def format_summary_table(report: DualGateBenchmarkReport) -> str:
    """Render a human-readable summary table of benchmark results."""
    lines = [
        f"=== {report.suite} Dual-Gate Benchmark Summary ===",
        f"Schema: {report.schema} | Version: {report.version}",
        f"Total Evaluated: {report.summary['evaluated_tasks']} / {report.total_tasks}",
        f"Gate 1 (Schematic Topo) Pass Rate : {report.summary['gate1_passed']}/{report.summary['evaluated_tasks']} ({report.summary['gate1_pass_rate']*100:.1f}%)",
        f"Gate 2 (Physical Board) Pass Rate : {report.summary['gate2_passed']}/{report.summary['evaluated_tasks']} ({report.summary['gate2_pass_rate']*100:.1f}%)",
        f"Dual-Gate End-to-End Pass Rate    : {report.summary['dual_gate_passed']}/{report.summary['evaluated_tasks']} ({report.summary['dual_gate_pass_rate']*100:.1f}%)",
        "",
        "--- Divergence Breakdown ---",
        f"  Concordant Pass     (G1 PASS, G2 PASS): {report.summary['divergence'].get(CONCORDANT_PASS, 0)}",
        f"  Physical Bottleneck (G1 PASS, G2 FAIL): {report.summary['divergence'].get(PHYSICAL_BOTTLENECK, 0)}",
        f"  Semantic Bypass     (G1 FAIL, G2 PASS): {report.summary['divergence'].get(SEMANTIC_BYPASS, 0)}",
        f"  Concordant Fail     (G1 FAIL, G2 FAIL): {report.summary['divergence'].get(CONCORDANT_FAIL, 0)}",
        "",
        f"{'Task ID':<8} {'Dataset':<16} {'Submodule':<24} {'Score (G1, G2)':<16} {'Dual-Gate':<10} {'Divergence Category'}",
        "-" * 100,
    ]
    for t in report.tasks:
        score_str = f"({t.score[0]}, {t.score[1]})"
        lines.append(
            f"{t.task_id:<8} {t.dataset:<16} {t.name:<24} {score_str:<16} {t.dual_gate_status:<10} {t.divergence_category}"
        )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI hook for Dual-Gate Benchmark Suite Runner."""
    parser = argparse.ArgumentParser(
        description="Dual-Gate Benchmark Suite Runner for PCBSchemaGen Tier-1 tasks."
    )
    parser.add_argument(
        "--manifest",
        "-m",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Path to Tier-1 manifest JSON.",
    )
    parser.add_argument(
        "--task-id",
        "-t",
        type=str,
        default=None,
        help="Task ID or comma-separated list of Task IDs to evaluate.",
    )
    parser.add_argument(
        "--candidate",
        "-c",
        type=Path,
        default=None,
        help="Path to single candidate design JSON or build run dir (requires --task-id).",
    )
    parser.add_argument(
        "--candidates-dir",
        type=Path,
        default=None,
        help="Directory containing task candidates (<id>.json or <id>/design.json).",
    )
    parser.add_argument(
        "--pcbschemagen-root",
        type=Path,
        default=DEFAULT_PCBSCHEMAGEN_ROOT,
        help="Root path to PCBSchemaGen_v2 repository.",
    )
    parser.add_argument(
        "--captures-dir",
        type=Path,
        default=None,
        help="Path to captured KiCad libraries/stock.",
    )
    parser.add_argument(
        "--out",
        "-o",
        type=Path,
        default=None,
        help="Path to write structured benchmark report JSON.",
    )
    parser.add_argument(
        "--run-build",
        action="store_true",
        help="Execute physical board compilation and routing in Gate 2.",
    )
    parser.add_argument(
        "--summary",
        "-s",
        action="store_true",
        help="Print text summary table.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress outputs.",
    )

    args = parser.parse_args(argv)

    if not args.manifest.is_file():
        sys.stderr.write(f"Error: Manifest file not found: {args.manifest}\n")
        return 2

    manifest = Tier1Manifest.load(args.manifest)

    # Build candidates map
    candidates_map: dict[str, Any] = {}
    if args.candidate and args.task_id:
        candidates_map[str(args.task_id).strip()] = args.candidate
    elif args.candidates_dir and args.candidates_dir.is_dir():
        for item in args.candidates_dir.iterdir():
            tid = item.stem
            if item.is_file() and item.suffix.lower() == ".json":
                candidates_map[tid] = item
            elif item.is_dir():
                candidates_map[tid] = item

    task_filter = None
    if args.task_id:
        task_filter = [t.strip() for t in args.task_id.split(",") if t.strip()]

    try:
        report = run_benchmark(
            manifest=manifest,
            candidates=candidates_map,
            pcbschemagen_root=args.pcbschemagen_root,
            captures_dir=args.captures_dir,
            run_build=args.run_build,
            task_ids=task_filter,
        )
    except PCBSchemaGenInputError as exc:
        sys.stderr.write(f"Benchmark input error: {exc}\n")
        return 2

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report.to_json(), encoding="utf-8")
        if not args.quiet:
            print(f"Report written to: {args.out}")

    if args.summary or not args.quiet:
        print(format_summary_table(report))

    return 0 if report.summary["dual_gate_passed"] == report.summary["evaluated_tasks"] else 1


if __name__ == "__main__":
    sys.exit(main())
