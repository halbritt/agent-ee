"""Immutable requirements, opt-in coverage audits and check-bound witnesses."""

from pathlib import Path

from .candidate import safe_relative
from .storage import digest, identity, read_json, write_json


def compile_baseline(scope, files, inputs=None):
    """Bind the complete baseline and verification criteria to captured bytes."""
    from .stages import declaration
    stages = declaration(scope, files)
    if scope.get("schema") == "agent-ee.scope/1":
        if "envelope" in scope:
            raise ValueError("a scope envelope requires agent-ee.scope/3 executable coverage")
        return None
    if scope.get("schema") not in ("agent-ee.scope/2", "agent-ee.scope/3"):
        raise ValueError("unknown scope schema")
    audited = scope["schema"] == "agent-ee.scope/3"
    baseline = scope.get("baseline")
    if not isinstance(baseline, dict) or set(baseline) != {
        "requirements", "targets", "reference_architectures"
    }:
        raise ValueError("scope/2 requires baseline requirements, targets and reference_architectures")
    from .findings import safe_id

    checks = {c["id"]: c for c in scope["checks"]}
    if len(checks) != len(scope["checks"]):
        raise ValueError("duplicate baseline check IDs")
    paths, ids, covered = set(), set(), set()
    requirements = baseline["requirements"]
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("baseline requires at least one requirement")
    for req in requirements:
        if not isinstance(req, dict) or set(req) != ({"id", "statement", "source", "checks", "intent"} if audited else {"id", "statement", "source", "checks"}):
            raise ValueError("requirement needs id, statement, source and checks" + (" and intent" if audited else ""))
        rid = safe_id(req["id"], "requirement id")
        if rid in ids:
            raise ValueError(f"duplicate requirement {rid}")
        ids.add(rid)
        if not isinstance(req["statement"], str) or not req["statement"].strip():
            raise ValueError(f"requirement {rid} has no statement")
        source = req["source"]
        if not isinstance(source, dict) or set(source) != {"path", "locator"}:
            raise ValueError(f"requirement {rid} needs source path and locator")
        if not isinstance(source["locator"], str) or not source["locator"].strip():
            raise ValueError(f"requirement {rid} has no source locator")
        paths.add(str(safe_relative(source["path"])))
        bound = req["checks"]
        if (not isinstance(bound, list) or not bound or
                any(not isinstance(c, str) or c not in checks for c in bound) or len(set(bound)) != len(bound)):
            raise ValueError(f"requirement {rid} must bind declared unique checks")
        covered.update(bound)
    if covered != set(checks):
        raise ValueError("every check must trace to a baseline requirement")
    for category in ("targets", "reference_architectures"):
        values = baseline[category]
        if not isinstance(values, list) or not values:
            raise ValueError(f"baseline {category} must name captured sources")
        paths.update(str(safe_relative(p)) for p in values)
    from .board_envelope import baseline_envelope
    # Before coverage, so an omitted or unbound envelope obligation is named.
    envelope = baseline_envelope(scope, files, inputs)
    if envelope is not None:
        paths.update(envelope["inputs"])
    coverage = None
    if audited:
        from .capabilities import compile_coverage
        coverage = compile_coverage(scope, files, inputs)
        for descriptor in coverage["checks"].values():
            paths.update(descriptor["immutable_inputs"])
    for spec in checks.values():
        params = spec.get("params", {})
        if spec.get("kind") in ("catalog", "part_selection"):
            from .catalog_checks import capability
            paths.update(capability(spec["kind"], params, scope, files, inputs, spec["id"])["immutable_inputs"])
        if spec.get("kind") == "symbol_pins":
            from .symbol_checks import capability
            paths.update(capability(params, files, inputs)["immutable_inputs"])
        if spec.get("kind") == "footprint_assignment":
            from .library_checks import capability
            paths.update(capability(params, files, inputs)["immutable_inputs"])
        if spec.get("kind") == "netlist_extra":
            from .netlist_extra import capability
            paths.update(capability(params, scope, files, inputs, spec["id"])["immutable_inputs"])
        if spec.get("kind") == "netclass_completeness":
            from .netclass_completeness import capability
            paths.update(capability(params, files, inputs)["immutable_inputs"])
        if spec.get("kind") == "routing_rules":
            from .routing_rules import capability
            descriptor = capability(params, scope, files, inputs, spec["id"])
            paths.update(descriptor["immutable_inputs"])
        if spec.get("kind") == "netlist_parity":
            from .netlist_parity import capability
            descriptor = capability(params, scope, files, inputs, spec["id"])
            paths.update(descriptor["immutable_inputs"])
        if spec.get("kind") == "fab_profile":
            from .fab_profile import capability
            descriptor = capability(params, scope, files, inputs, spec["id"])
            paths.update(descriptor["immutable_inputs"])
        if spec.get("kind") == "hardware_interface":
            from .hardware_interface import capability
            descriptor = capability(params, scope, files, inputs, spec["id"])
            paths.update(descriptor["immutable_inputs"])
        if spec.get("kind") == "active_spice":
            from .active_spice import capability
            descriptor = capability(params, files)
            paths.update(descriptor["immutable_inputs"])
        if spec.get("kind") == "dc_path":
            from .dc_path import load_inputs
            _, dependencies = load_inputs(params, files, inputs)
            paths.update(dependencies)
        if spec.get("kind") == "placement":
            from .placement_check import parameters
            paths.update(str(safe_relative(p)) for p in parameters(params))
        if spec.get("kind") == "passive_corners":
            paths.add(str(safe_relative(params["qualification"])))
        if spec.get("kind") == "measurements":
            paths.add(str(safe_relative(params["adapter"])))
            paths.update(str(safe_relative(p)) for p in params["tool"].get("inputs", []))
        paths.update(str(safe_relative(p)) for p in params.get("calibration_inputs", []))
    from .datasheet_facts import verification_inputs
    paths.update(verification_inputs(scope, inputs))
    paths.update(str(safe_relative(p)) for p in scope.get("requirements", []))
    if not paths <= files.keys():
        raise ValueError(f"baseline sources not captured: {sorted(paths - files.keys())}")
    if stages is not None and paths.intersection(s['path'] for s in stages['subjects'].values()):
        raise ValueError('stage subjects cannot be immutable verification dependencies')
    if audited and paths.intersection({scope.get("board"), scope.get("schematic")}):
        raise ValueError("native design cannot be an immutable verification dependency")
    value = {"schema": "agent-ee.baseline/1", **baseline,
             "files": {p: files[p] for p in sorted(paths)}, "criteria": scope["checks"]}
    if stages is not None:
        value['stages'] = stages
    if envelope is not None:
        value["envelope"] = envelope["record"]
    if audited:
        value.update(schema="agent-ee.baseline/2", coverage=coverage)
    return {**value, "baseline_id": identity(value)}


def load_baseline(run):
    manifest = read_json(Path(run) / "candidate.json")
    expected = compile_baseline(manifest["scope"], manifest["files"], Path(run) / "inputs")
    if expected is None:
        return None
    if read_json(Path(run) / "baseline.json") != expected:
        raise ValueError("immutable baseline changed")
    return expected


def evidence_seal(run, checks):
    """Seal coordinator-produced checks before any reviewer can cite them."""
    run = Path(run)
    baseline = load_baseline(run)
    if baseline is None:
        return
    manifest = read_json(run / "candidate.json")
    if {c["id"] for c in checks} != {c["id"] for c in baseline["criteria"]}:
        raise ValueError("evidence omits baseline checks")
    from .findings import validate_check_result, validate_citation_path

    receipts = {}
    for check in checks:
        validate_check_result(check, run)
        if check.get("candidate_id") != manifest["candidate_id"]:
            raise ValueError("check evidence belongs to a different candidate")
        from .stages import verify_gate
        verify_gate(run, check, manifest, baseline)
        path = f"checks/{check['id']}.json"
        if read_json(run / path) != check:
            raise ValueError("check differs from retained result")
        evidence = [e["path"] if isinstance(e, dict) else e for e in check["evidence"]]
        for rel in evidence:
            validate_citation_path(rel, run)
            if not rel.startswith(f"work/{check['id']}/") and not any(
                    rel in receipt["artifacts"] for receipt in receipts.values()):
                raise ValueError("check evidence must come from its own or a sealed prerequisite workspace")
        receipts[check["id"]] = {"result": digest(run / path),
            "artifacts": {p: digest(run / p) for p in evidence}}
    return {
        "schema": "agent-ee.evidence/1", "candidate_id": manifest["candidate_id"],
        "baseline_id": baseline["baseline_id"], "checks": receipts}


def freeze_evidence(run, checks):
    seal = evidence_seal(run, checks)
    if seal is not None:
        write_json(Path(run) / "work/verification/evidence.json", seal)


def verify_evidence(run, checks, *, required):
    if load_baseline(run) is None:
        return
    path = Path(run) / "work/verification/evidence.json"
    if not path.exists() and not required:
        return
    if read_json(path) != evidence_seal(run, checks):
        raise ValueError("evidence seal contradicts retained check artifacts")


def validate_witness_shape(witnesses):
    from .findings import safe_id
    if not isinstance(witnesses, list):
        raise ValueError("witnesses must be a list")
    for witness in witnesses:
        if not isinstance(witness, dict) or set(witness) != {"requirement_id", "check_id"}:
            raise ValueError("witness requires requirement_id and check_id")
        safe_id(witness["requirement_id"], "requirement id")
        safe_id(witness["check_id"], "check id")


def validate_witnesses(run, witnesses, evidence):
    """A claim can establish only a failed criterion bound to its requirement."""
    run = Path(run)
    baseline = load_baseline(run)
    if baseline is None:
        return
    validate_witness_shape(witnesses)
    if not witnesses:
        raise ValueError("accepted defect requires requirement/check witnesses")
    seal = read_json(run / "work/verification/evidence.json")
    manifest = read_json(run / "candidate.json")
    if seal["baseline_id"] != baseline["baseline_id"] or seal["candidate_id"] != manifest["candidate_id"]:
        raise ValueError("evidence identity differs from baseline/candidate")
    requirements = {r["id"]: r for r in baseline["requirements"]}
    cited = {e["path"] for e in evidence}
    for witness in witnesses:
        if not isinstance(witness, dict) or set(witness) != {"requirement_id", "check_id"}:
            raise ValueError("witness requires requirement_id and check_id")
        rid, cid = witness["requirement_id"], witness["check_id"]
        if rid not in requirements or cid not in requirements[rid]["checks"]:
            raise ValueError("witness does not trace to a baseline requirement")
        if baseline["schema"] == "agent-ee.baseline/2":
            coverage = baseline["coverage"]["requirements"][rid]
            if coverage["intent"] != "acceptance" or cid not in coverage["acceptance_checks"]:
                raise ValueError("witness requires audited acceptance capability")
        path = f"checks/{cid}.json"
        receipt = seal["checks"][cid]
        if path not in cited or digest(run / path) != receipt["result"]:
            raise ValueError("witness needs an unchanged cited check result")
        check = read_json(run / path)
        if check["execution"] != "ok" or check["outcome"] != "defect":
            raise ValueError("witness must establish a freshly failed criterion")
        if not receipt["artifacts"]:
            raise ValueError("witness has no reproducible tool evidence")
        for rel, sha in receipt["artifacts"].items():
            if digest(run / rel) != sha:
                raise ValueError("witness tool evidence changed")
