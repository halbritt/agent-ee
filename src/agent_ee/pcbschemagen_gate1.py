"""Gate 1 Topo Verifier Adapter for PCBSchemaGen benchmark verification.

Translates candidate circuits from `agent-ee.design/1` or native KiCad XML
netlists into PCBSchemaGen `framework/topo` snapshot format and runs deterministic,
zero-LLM topological and ERC verification.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Optional, Sequence, Union

DEFAULT_PCBSCHEMAGEN_ROOT = Path(
    os.environ.get("PCBSCHEMAGEN_ROOT", str(Path.home() / "git/PCBSchemaGen_v2"))
)
PCBSCHEMAGEN_REPOSITORY = "https://github.com/HZou9/PCBSchemaGen_v2"
PCBSCHEMAGEN_COMMIT = "e07c545f4c01a8026480b1093ee503a926ce2be5"
SCHEMA = "agent-ee.pcbschemagen-gate1/1"


class PCBSchemaGenInputError(RuntimeError):
    """The external verifier cannot be used as a qualified benchmark input."""


class PCBSchemaGenUnavailable(PCBSchemaGenInputError):
    """The separately provisioned Gate 1 verifier input is absent."""


def check_pcbschemagen_checkout(root: Union[str, Path]) -> Path:
    """Require the pinned, clean external verifier and its knowledge graphs."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise PCBSchemaGenUnavailable(
            f"PCBSchemaGen verifier missing at {root}; provision {PCBSCHEMAGEN_REPOSITORY} "
            f"at {PCBSCHEMAGEN_COMMIT} and set PCBSCHEMAGEN_ROOT or --pcbschemagen-root"
        )
    def git(*args):
        result = subprocess.run(['git', '--no-optional-locks', '-C', str(root), *args],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise PCBSchemaGenInputError(f"PCBSchemaGen checkout at {root} cannot be verified: {result.stderr.strip()}")
        return result.stdout.strip()
    if git('rev-parse', 'HEAD') != PCBSCHEMAGEN_COMMIT:
        raise PCBSchemaGenInputError(f"PCBSchemaGen checkout must be at {PCBSCHEMAGEN_COMMIT}: {root}")
    if git('status', '--porcelain', '--untracked-files=all'):
        raise PCBSchemaGenInputError(f"PCBSchemaGen checkout must be clean: {root}")
    for name in ('framework/topo/kg_loader.py', 'framework/task_config.yaml',
                 'component.json', 'kg_component.json', 'kg/kg_open_schematics.json'):
        if not (root / name).is_file():
            raise PCBSchemaGenInputError(f"PCBSchemaGen pinned input missing: {root / name}")
    for module in ('yaml', 'networkx'):
        if importlib.util.find_spec(module) is None:
            raise PCBSchemaGenUnavailable(f"PCBSchemaGen Python dependency missing: {module}; "
                                          "install the pinned checkout's requirements.txt")
    return root


def _ensure_topo_imports(pcbschemagen_root: Union[str, Path] = DEFAULT_PCBSCHEMAGEN_ROOT) -> None:
    root = check_pcbschemagen_checkout(pcbschemagen_root)
    for name, module in tuple(sys.modules.items()):
        if name.startswith('framework.topo') and getattr(module, '__file__', None):
            if not Path(module.__file__).resolve().is_relative_to(root):
                raise PCBSchemaGenInputError(f"PCBSchemaGen module already loaded from another checkout: {name}")
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


@dataclass
class Gate1Result:
    """Structured result of Gate 1 topological verification."""

    passed: bool
    status: str
    reward: float
    errors: list[Any]
    warnings: list[Any]
    report: dict[str, Any]
    snapshot: dict[str, Any]

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.report, indent=indent, default=str)



class Gate1KGStore:
    """Unified Knowledge Graph store covering PCBBench and Open-Schematics-Eval."""

    def __init__(
        self,
        base_dir: Union[str, Path] = DEFAULT_PCBSCHEMAGEN_ROOT,
        unified_kg_path: Optional[Union[str, Path]] = None,
    ) -> None:
        _ensure_topo_imports(base_dir)
        from framework.topo.kg_loader import KGStore

        self.base_dir = str(Path(base_dir).resolve())
        self._upstream_store = KGStore(self.base_dir)
        self.component_map: dict[str, dict[str, Any]] = dict(self._upstream_store.component_map)
        self.kg_component_map: dict[str, dict[str, Any]] = dict(
            self._upstream_store.kg_component_map
        )

        # Merge open_schematics KG if available
        if unified_kg_path is None:
            unified_kg_path = os.path.join(self.base_dir, "kg", "kg_open_schematics.json")
        if os.path.exists(unified_kg_path):
            with open(unified_kg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for c in data.get("components", []):
                cid = c.get("id")
                if not cid:
                    continue
                if cid not in self.component_map:
                    self.component_map[cid] = c
                if cid not in self.kg_component_map:
                    self.kg_component_map[cid] = c
                else:
                    existing = self.kg_component_map[cid]
                    if not existing.get("pin_roles") and c.get("pin_roles"):
                        existing["pin_roles"] = c["pin_roles"]
                    if not existing.get("generic_constraints") and c.get("generic_constraints"):
                        existing["generic_constraints"] = c["generic_constraints"]
                    if not existing.get("pins") and c.get("pins"):
                        existing["pins"] = c["pins"]

    def get_component(self, part_id: str) -> Optional[dict[str, Any]]:
        part_id = str(part_id).strip()
        return self.kg_component_map.get(part_id)

    def get_component_info(self, part_id: str) -> Optional[dict[str, Any]]:
        part_id = str(part_id).strip()
        return self.component_map.get(part_id)

    def get_category(self, part_id: str) -> Optional[str]:
        comp = self.get_component(part_id)
        if comp and comp.get("category"):
            return comp.get("category")
        info = self.get_component_info(part_id)
        if info and info.get("category"):
            return info.get("category")
        return None

    def get_pin_roles(self, part_id: str) -> dict[str, str]:
        comp = self.get_component(part_id)
        if comp and comp.get("pin_roles"):
            return dict(comp.get("pin_roles", {}))
        return {}

    def get_constraints(self, part_id: str) -> list[dict[str, Any]]:
        comp = self.get_component(part_id)
        if comp and comp.get("generic_constraints"):
            return list(comp.get("generic_constraints", []))
        return []

    def has_component(self, part_id: str) -> bool:
        part_id = str(part_id).strip()
        return part_id in self.component_map or part_id in self.kg_component_map



def is_external_header(
    ref: str,
    part_id: str = "",
    lib_id: Optional[str] = None,
    footprint: Optional[str] = None,
) -> bool:
    """Check if component is an external pin header (e.g. 2.54 mm header)."""
    ref_u = (ref or "").strip().upper()
    part_u = (part_id or "").strip().upper()
    lib_u = (lib_id or "").strip().upper()
    fp_u = (footprint or "").strip().upper()

    if "PINHEADER" in part_u or "PINHEADER" in fp_u or "PINHEADER" in lib_u:
        return True
    if "CONNECTOR_PINHEADER" in fp_u or "CONNECTOR_PINHEADER" in lib_u:
        return True
    if ref_u.startswith("J") or ref_u.startswith("P"):
        if "CONN" in part_u or "CONN" in lib_u or "CONN" in fp_u or "HEADER" in part_u:
            return True
        if not part_id and not lib_id and not footprint:
            return True
    return False


def resolve_canonical_part_id(
    ref: str,
    value: str = "",
    lib_id: Optional[str] = None,
    footprint: Optional[str] = None,
    kg_store: Optional[Gate1KGStore] = None,
) -> str:
    """Map component to canonical Knowledge Graph part ID."""
    ref_clean = (ref or "").strip()
    ref_prefix = ref_clean[:1].upper()

    if is_external_header(ref_clean, value, lib_id=lib_id, footprint=footprint):
        # Normalize header representation
        if footprint and "PinHeader" in footprint:
            m = re.search(r"(PinHeader_\d+x\d+)", footprint)
            if m:
                return m.group(1)
        if lib_id and "Conn_" in lib_id:
            m = re.search(r"Conn_0?(\d+x\d+)", lib_id)
            if m:
                return f"PinHeader_{m.group(1)}"
        if value and "PinHeader" in value:
            return value
        return "PinHeader_1x02"

    # Passives
    if lib_id:
        lib_symbol = lib_id.split(":")[-1].strip()
        if lib_symbol in ("R", "C", "L", "D", "LED", "C_Polarized", "FerriteBead"):
            if lib_symbol == "FerriteBead":
                return "L"
            if lib_symbol in ("C_Polarized",):
                return "C"
            if lib_symbol in ("LED",):
                return "D"
            return lib_symbol
    if ref_prefix in ("R", "C", "L", "D"):
        return ref_prefix

    # Active ICs: extract symbol name and value
    candidates: list[str] = []
    if value and value.strip():
        candidates.append(value.strip())
    if lib_id:
        symbol = lib_id.split(":")[-1].strip()
        if symbol:
            candidates.append(symbol)

    if kg_store:
        # 1. Exact match
        for cand in candidates:
            if kg_store.has_component(cand):
                return cand

        # 2. Case-insensitive match
        cand_map = {cid.upper(): cid for cid in kg_store.component_map.keys()}
        for cand in candidates:
            if cand.upper() in cand_map:
                return cand_map[cand.upper()]

        # 3. Longest prefix/suffix match in KG
        sorted_kg_ids = sorted(kg_store.component_map.keys(), key=len, reverse=True)
        for cand in candidates:
            cand_u = cand.upper()
            for kg_id in sorted_kg_ids:
                kg_u = kg_id.upper()
                if cand_u.startswith(kg_u) or kg_u.startswith(cand_u):
                    return kg_id

    # Fallback to symbol or value or ref
    for cand in candidates:
        if cand:
            return cand
    return ref_clean



def design_to_snapshot(
    design_input: Union[dict[str, Any], Path, str],
    kg_store: Optional[Gate1KGStore] = None,
) -> dict[str, Any]:
    """Translate an agent-ee.design/1 specification into PCBSchemaGen snapshot format."""
    if isinstance(design_input, (str, Path)):
        p = Path(design_input)
        if p.exists() and p.is_file():
            design = json.loads(p.read_text(encoding="utf-8"))
        else:
            design = json.loads(str(design_input))
    else:
        design = design_input

    parts = design.get("parts", {})
    nets_dict = design.get("nets", {})
    no_connects = set(design.get("no_connects", []))

    # Map terminal strings "REF.PIN" -> net_name
    terminal_net_map: dict[str, str] = {}
    for net_name, terminals in nets_dict.items():
        for term in terminals:
            terminal_net_map[str(term)] = str(net_name)

    components: list[dict[str, Any]] = []
    comp_by_ref: dict[str, dict[str, Any]] = {}

    for ref, part_data in parts.items():
        val = str(part_data.get("value", ""))
        lib_id = part_data.get("lib_id")
        fp = part_data.get("footprint")
        part_id = resolve_canonical_part_id(ref, val, lib_id=lib_id, footprint=fp, kg_store=kg_store)

        pins: list[dict[str, Any]] = []
        kg_info = kg_store.get_component_info(part_id) if kg_store else None
        is_passive = part_id in ("R", "C", "L", "D")

        if kg_info and kg_info.get("pins"):
            for p in kg_info["pins"]:
                pin_id = str(p.get("num", ""))
                pin_name = str(p.get("name", pin_id))
                term_by_id = f"{ref}.{pin_id}"
                term_by_name = f"{ref}.{pin_name}"
                net = terminal_net_map.get(term_by_id) or terminal_net_map.get(term_by_name)
                if not net:
                    if term_by_id in no_connects or term_by_name in no_connects:
                        net = "NC"
                    else:
                        net = None
                pins.append({"pin_id": pin_id, "pin_name": pin_name, "net": net})
        elif is_passive:
            for pin_num in ("1", "2"):
                term = f"{ref}.{pin_num}"
                net = terminal_net_map.get(term)
                if not net and term in no_connects:
                    net = "NC"
                pins.append({"pin_id": pin_num, "pin_name": "~", "net": net})
        else:
            # Connectors or uncataloged parts: find all terminals mentioned in design
            seen_pins: set[str] = set()
            for term in list(terminal_net_map.keys()) + list(no_connects):
                if term.startswith(f"{ref}."):
                    seen_pins.add(term.split(".", 1)[1])
            if not seen_pins:
                seen_pins = {"1", "2"}
            for pin_id in sorted(seen_pins, key=lambda s: int(s) if s.isdigit() else s):
                term = f"{ref}.{pin_id}"
                net = terminal_net_map.get(term)
                if not net and term in no_connects:
                    net = "NC"
                pins.append({"pin_id": pin_id, "pin_name": pin_id, "net": net})

        comp_entry = {
            "ref": ref,
            "part_id": part_id,
            "value": val or None,
            "pins": pins,
        }
        components.append(comp_entry)
        comp_by_ref[ref] = comp_entry

    # Build nets
    nets: list[dict[str, Any]] = []
    for net_name, terminals in nets_dict.items():
        endpoints: list[dict[str, Any]] = []
        for term in terminals:
            parts_term = str(term).split(".", 1)
            if len(parts_term) != 2:
                continue
            r, p_id = parts_term
            p_name = p_id
            comp = comp_by_ref.get(r)
            if comp:
                for cp in comp.get("pins", []):
                    if cp.get("pin_id") == p_id:
                        p_name = cp.get("pin_name", p_id)
                        break
            endpoints.append({"ref": r, "pin_id": p_id, "pin_name": p_name})
        nets.append({"name": str(net_name), "endpoints": endpoints})

    return {"components": components, "nets": nets}


def netlist_to_snapshot(
    netlist_input: Union[Path, str],
    kg_store: Optional[Gate1KGStore] = None,
) -> dict[str, Any]:
    """Translate native KiCad XML netlist export into PCBSchemaGen snapshot format."""
    from agent_ee.native_netlist import read_native_inventory

    path = Path(netlist_input)
    inventory = read_native_inventory(path, pinless=True)

    # Map (ref, pin_str) -> (net_name, is_connected)
    term_to_net: dict[tuple[str, str], str] = {}
    for nid, net_data in inventory.get("nets", {}).items():
        name = net_data.get("name", "")
        is_nc = (
            net_data.get("no_connect_marker", False)
            or net_data.get("auto_unconnected_singleton", False)
            or name.startswith("unconnected-(")
        )
        assigned_name = "NC" if is_nc else name
        for r, p in net_data.get("terminals", ()):
            term_to_net[(str(r), str(p))] = assigned_name

    components: list[dict[str, Any]] = []
    comp_by_ref: dict[str, dict[str, Any]] = {}

    for ref, comp_data in inventory.get("components", {}).items():
        val = str(comp_data.get("value", ""))
        lib_id = comp_data.get("lib_id")
        fp = comp_data.get("footprint")
        part_id = resolve_canonical_part_id(ref, val, lib_id=lib_id, footprint=fp, kg_store=kg_store)
        is_passive = part_id in ("R", "C", "L", "D")

        pins: list[dict[str, Any]] = []
        for pin_num, meta in comp_data.get("pin_metadata", {}).items():
            pin_id = str(pin_num)
            pin_name = "~" if is_passive else str(meta.get("name") or pin_id)
            net = term_to_net.get((str(ref), pin_id))
            pins.append({"pin_id": pin_id, "pin_name": pin_name, "net": net})

        comp_entry = {
            "ref": ref,
            "part_id": part_id,
            "value": val or None,
            "pins": pins,
        }
        components.append(comp_entry)
        comp_by_ref[ref] = comp_entry

    nets: list[dict[str, Any]] = []
    for nid, net_data in inventory.get("nets", {}).items():
        name = net_data.get("name", "")
        if (
            net_data.get("no_connect_marker", False)
            or net_data.get("auto_unconnected_singleton", False)
            or name.startswith("unconnected-(")
        ):
            continue
        endpoints: list[dict[str, Any]] = []
        for r, p in net_data.get("terminals", ()):
            r_str = str(r)
            p_str = str(p)
            p_name = p_str
            comp = comp_by_ref.get(r_str)
            if comp:
                for cp in comp.get("pins", []):
                    if cp.get("pin_id") == p_str:
                        p_name = cp.get("pin_name", p_str)
                        break
            endpoints.append({"ref": r_str, "pin_id": p_str, "pin_name": p_name})
        nets.append({"name": str(name), "endpoints": endpoints})

    return {"components": components, "nets": nets}



def filter_headers_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of snapshot with external pin headers excluded from components.

    Net endpoints on remaining components remain intact so circuit topology is preserved.
    """
    filtered_comps = [
        c
        for c in snapshot.get("components", [])
        if not is_external_header(c.get("ref", ""), c.get("part_id", ""))
    ]
    return {"components": filtered_comps, "nets": snapshot.get("nets", [])}


def _error_to_dict(err: Any) -> dict[str, Any]:
    if hasattr(err, "phase"):
        return {
            "phase": getattr(err, "phase", ""),
            "severity": getattr(err, "severity", "critical"),
            "category": getattr(err, "category", ""),
            "component": getattr(err, "component", None),
            "pin": getattr(err, "pin", None),
            "net": getattr(err, "net", None),
            "constraint": getattr(err, "constraint", None),
            "message": str(err),
            "suggestion": getattr(err, "suggestion", None),
            "fingerprint": err.fingerprint() if hasattr(err, "fingerprint") else f"{getattr(err, 'category', '')}:{getattr(err, 'component', '')}:{getattr(err, 'pin', '')}",
        }
    return {
        "phase": "unknown",
        "severity": "critical",
        "category": "generic_error",
        "message": str(err),
    }


def verify_gate1(
    design_input: Union[dict[str, Any], Path, str, None] = None,
    task_id: Optional[Union[int, str]] = None,
    *,
    netlist_xml: Optional[Union[Path, str]] = None,
    captures_dir: Optional[Union[Path, str]] = None,
    pcbschemagen_root: Union[Path, str] = DEFAULT_PCBSCHEMAGEN_ROOT,
    std_snapshot: Optional[Union[dict[str, Any], Path, str]] = None,
    rules: Optional[Union[list[dict[str, Any]], Path, str]] = None,
    task_name: Optional[str] = None,
) -> Gate1Result:
    """Run deterministic Gate 1 schematic and topology verification."""
    _ensure_topo_imports(pcbschemagen_root)
    from framework.topo.build_topology import augment_snapshot
    from framework.topo.complex_task_validator import is_complex_task, validate_complex_task
    from framework.topo.error_types import compute_reward_from_errors
    from framework.topo.match_skeleton import check_graph_similarity
    from framework.topo.phase2_checks import run_phase2_checks
    from framework.topo.rule_checker import check_rules

    kg_store = Gate1KGStore(pcbschemagen_root)

    # 1. Translate input to snapshot
    if netlist_xml is not None:
        raw_snapshot = netlist_to_snapshot(netlist_xml, kg_store=kg_store)
    elif design_input is not None:
        if isinstance(design_input, dict) and "components" in design_input and "nets" in design_input:
            raw_snapshot = design_input
        else:
            raw_snapshot = design_to_snapshot(design_input, kg_store=kg_store)
    else:
        raise ValueError("Either design_input or netlist_xml must be provided")

    # 2. Augment snapshot with KG pin roles and categories
    augmented_snapshot = augment_snapshot(raw_snapshot, kg_store)

    # 3. Numeric task_id normalization if possible
    tid_norm: Optional[Union[int, str]] = task_id
    if isinstance(task_id, str) and task_id.isdigit():
        tid_norm = int(task_id)

    phase2_errors: list[Any] = []
    rule_errors: list[Any] = []
    si_errors: list[Any] = []
    warnings: list[Any] = []

    # 4. Execute topological checks
    if tid_norm is not None and is_complex_task(tid_norm):
        passed_comp, comp_errs, comp_warns = validate_complex_task(
            augmented_snapshot, tid_norm, kg_store
        )
        phase2_errors.extend(comp_errs)
        warnings.extend(comp_warns)
    else:
        p2_errs = run_phase2_checks(augmented_snapshot, kg_store, task_id=tid_norm)
        phase2_errors.extend(p2_errs)

        # Rules check
        loaded_rules: list[dict[str, Any]] = []
        if rules is not None:
            if isinstance(rules, (str, Path)):
                with open(rules, "r", encoding="utf-8") as f:
                    loaded_rules = json.load(f)
            else:
                loaded_rules = list(rules)
        elif std_snapshot is not None:
            from framework.topo.rule_extractor import build_rules

            if isinstance(std_snapshot, (str, Path)):
                with open(std_snapshot, "r", encoding="utf-8") as f:
                    std_snap_dict = json.load(f)
            else:
                std_snap_dict = std_snapshot
            loaded_rules = build_rules(std_snap_dict)

        if loaded_rules:
            r_errs = check_rules(augmented_snapshot, loaded_rules, task_id=tid_norm)
            rule_errors.extend(r_errs)

        # Skeleton / SI check
        if std_snapshot is not None:
            if isinstance(std_snapshot, (str, Path)):
                with open(std_snapshot, "r", encoding="utf-8") as f:
                    std_snap_dict = json.load(f)
            else:
                std_snap_dict = std_snapshot
            aug_std = augment_snapshot(std_snap_dict, kg_store)
            filtered_std = filter_headers_from_snapshot(aug_std)
            filtered_gen = filter_headers_from_snapshot(augmented_snapshot)
            s_errs = check_graph_similarity(filtered_std, filtered_gen, task_id=tid_norm)
            si_errors.extend(s_errs)

    all_errors = phase2_errors + rule_errors + si_errors
    critical_errors = [
        e for e in all_errors if getattr(e, "severity", "critical") == "critical"
    ]
    warning_errors = (
        [e for e in all_errors if getattr(e, "severity", "") == "warning"] + warnings
    )

    passed = len(critical_errors) == 0
    status = "PASS" if passed else "FAIL"

    reward = compute_reward_from_errors(
        has_code=True,
        syntax_ok=True,
        erc_ok=(len(phase2_errors) == 0),
        kg_errors=phase2_errors,
        rule_errors=rule_errors,
        si_errors=si_errors,
    )

    # Component matching & net discrepancies metadata
    comp_matching: dict[str, Any] = {}
    for comp in augmented_snapshot.get("components", []):
        r = comp.get("ref", "")
        pid = comp.get("part_id", "")
        comp_matching[r] = {
            "ref": r,
            "part_id": pid,
            "category": comp.get("category"),
            "pins_count": len(comp.get("pins", [])),
            "is_external_header": is_external_header(r, pid),
        }

    net_discrepancies: list[str] = [
        e.message for e in critical_errors if "net" in getattr(e, "category", "") or "supply" in getattr(e, "category", "")
    ]

    llm_lines: list[str] = [f"## Gate 1 Verification {'Passed' if passed else 'Failed'}"]
    if not passed:
        llm_lines.append("\n### Critical Errors (must fix):")
        for err in critical_errors:
            if hasattr(err, "to_feedback"):
                llm_lines.append(f"- {err.to_feedback()}")
            else:
                llm_lines.append(f"- [CRITICAL] {err}")
    if warning_errors:
        llm_lines.append("\n### Warnings:")
        for w in warning_errors:
            llm_lines.append(f"- [WARNING] {w}")

    report = {
        "schema": SCHEMA,
        "verifier": {"repository": PCBSCHEMAGEN_REPOSITORY, "commit": PCBSCHEMAGEN_COMMIT},
        "task_id": task_id,
        "task_name": task_name or str(task_id or "UNKNOWN"),
        "status": status,
        "passed": passed,
        "reward": float(reward),
        "stats": {
            "critical_errors": len(critical_errors),
            "warnings": len(warning_errors),
            "components_count": len(augmented_snapshot.get("components", [])),
            "nets_count": len(augmented_snapshot.get("nets", [])),
        },
        "erc_checks": [_error_to_dict(e) for e in phase2_errors],
        "rule_checks": [_error_to_dict(e) for e in rule_errors],
        "component_matching": comp_matching,
        "net_discrepancies": net_discrepancies,
        "errors": [_error_to_dict(e) for e in critical_errors],
        "warnings": [_error_to_dict(w) for w in warning_errors],
        "llm_feedback": "\n".join(llm_lines),
    }

    return Gate1Result(
        passed=passed,
        status=status,
        reward=reward,
        errors=critical_errors,
        warnings=warning_errors,
        report=report,
        snapshot=augmented_snapshot,
    )



def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI hook for Gate 1 Topo Verifier Adapter."""
    parser = argparse.ArgumentParser(
        description="Gate 1 Topo Verifier Adapter for PCBSchemaGen benchmark verification."
    )
    parser.add_argument(
        "--design",
        "-d",
        type=Path,
        default=None,
        help="Path to agent-ee.design/1 JSON file.",
    )
    parser.add_argument(
        "--netlist",
        "-n",
        type=Path,
        default=None,
        help="Path to native KiCad XML netlist.",
    )
    parser.add_argument(
        "--task-id",
        "-t",
        type=str,
        default=None,
        help="Task ID in PCBSchemaGen benchmark suite (e.g. 6 or BUCK_AUX_LOGIC).",
    )
    parser.add_argument(
        "--task-name",
        type=str,
        default=None,
        help="Optional descriptive name for the task.",
    )
    parser.add_argument(
        "--pcbschemagen-root",
        type=Path,
        default=DEFAULT_PCBSCHEMAGEN_ROOT,
        help="Root path to PCBSchemaGen_v2 repository.",
    )
    parser.add_argument(
        "--captures",
        type=Path,
        default=None,
        help="Path to directory containing captured KiCad symbols.",
    )
    parser.add_argument(
        "--std-snapshot",
        type=Path,
        default=None,
        help="Path to reference design snapshot JSON file.",
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=None,
        help="Path to topological rules JSON file.",
    )
    parser.add_argument(
        "--out",
        "-o",
        type=Path,
        default=None,
        help="Path to write structured Gate 1 diagnostic report JSON.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress non-error output.",
    )

    args = parser.parse_args(argv)

    if not args.design and not args.netlist:
        sys.stderr.write("Error: Either --design or --netlist must be specified.\n")
        return 2

    try:
        result = verify_gate1(
            design_input=args.design,
            netlist_xml=args.netlist,
            task_id=args.task_id,
            task_name=args.task_name,
            pcbschemagen_root=args.pcbschemagen_root,
            captures_dir=args.captures,
            std_snapshot=args.std_snapshot,
            rules=args.rules,
        )
    except Exception as exc:
        sys.stderr.write(f"Verification execution error: {exc}\n")
        return 2

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(result.to_json(), encoding="utf-8")

    if not args.quiet:
        print(f"Gate 1 Status: {result.status} (reward: {result.reward:.2f})")
        if not result.passed:
            print(result.report["llm_feedback"])

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
