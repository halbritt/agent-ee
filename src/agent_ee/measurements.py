"""Tool-produced scalar measurements and explicit cross-domain margin checks."""

from .check_registry import register as _register_check

import math
import re
from pathlib import Path

from .baseline import load_baseline
from .candidate import safe_relative
from .storage import digest, read_json, write_json


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def evaluate_limits(metrics, limits):
    if not isinstance(limits, dict) or not limits:
        raise ValueError("measurement check requires explicit limits")
    margins = {}
    for name, limit in limits.items():
        metric = metrics[name]
        value = finite(metric["value"], name)
        if metric["unit"] != limit["unit"] or metric["condition"] != limit["condition"]:
            raise ValueError(f"{name}: unit or operating condition mismatch")
        lower = finite(metric.get("lower", value), name + " lower")
        upper = finite(metric.get("upper", value), name + " upper")
        if not lower <= value <= upper:
            raise ValueError(f"{name}: inconsistent measurement interval")
        bounds = [key for key in ("min", "max") if key in limit]
        if not bounds:
            raise ValueError(f"{name}: no acceptance bounds")
        if "min" in limit and "max" in limit and limit["min"] > limit["max"]:
            raise ValueError(f"{name}: reversed bounds")
        margins[name] = {key: lower - finite(limit[key], key) if key == "min"
                         else finite(limit[key], key) - upper for key in bounds}
    return margins


def finish(ctx, metrics, limits):
    from .empirical import calibrate
    metrics = calibrate(ctx, metrics)
    margins = evaluate_limits(metrics, limits)
    ctx.result["details"].update(metrics=metrics, margins=margins)
    failed = [name for name, bounds in margins.items() if any(m < 0 for m in bounds.values())]
    ctx.result.update(execution="ok", outcome="defect" if failed else "pass",
                      summary="measurement limits violated: " + ", ".join(failed) if failed
                      else "all declared measurement margins satisfied")


def check_measurements(ctx):
    """Run a captured adapter; the coordinator, never the adapter, judges limits."""
    from .checks import CheckError

    try:
        adapter = str(safe_relative(ctx.params["adapter"]))
        if adapter not in ctx.manifest["files"]:
            raise ValueError("measurement adapter must be captured")
        request = {"schema": "agent-ee.measurement-request/1",
            "candidate_id": ctx.manifest["candidate_id"], "files": ctx.manifest["files"],
            "baseline": load_baseline(ctx.run_dir), "parameters": ctx.params.get("parameters", {})}
        from .empirical import calibration_for
        request["empirical"] = calibration_for(ctx.run_dir, ctx.scope, ctx.manifest["files"], provenance_only=True)
        write_json(ctx.work / "request.json", request)
        request_sha256 = digest(ctx.work / "request.json")
        ctx.evidence("request.json")
        producer, raw_hashes = execute_measurement_tool(ctx, request)
        ctx.execute(["/usr/bin/python3", ctx.candidate_path(adapter), "/work/request.json",
                     "/work/measurements.json"], timeout=ctx.params.get("timeout_s", 600),
                     extra_ro=[(ctx.work / "tool", "/work/tool"),
                               (ctx.work / "request.json", "/work/request.json")])
        if digest(ctx.work / "request.json") != request_sha256:
            raise ValueError("measurement parser altered coordinator request")
        ctx.evidence("measurements.json", "stdout.log", "stderr.log")
        for rel, sha in raw_hashes.items():
            if digest(ctx.work / rel) != sha:
                raise ValueError("measurement parser altered raw tool evidence")
        report = ctx.load_json("measurements.json", "measurement adapter")
        if report.get("schema") != "agent-ee.measurements/1":
            raise ValueError("expected agent-ee.measurements/1")
        if report.get("candidate_id") != ctx.manifest["candidate_id"] or report.get("files") != ctx.manifest["files"]:
            raise ValueError("measurement report is not bound to this candidate's inputs")
        if report.get("domain") != producer["domain"]:
            raise ValueError("parser domain differs from baseline tool declaration")
        raw = report.get("artifacts")
        if not isinstance(raw, list) or not raw:
            raise ValueError("parser must cite raw tool artifacts")
        for rel in raw:
            rel = str(safe_relative(rel))
            if rel not in raw_hashes or rel == "tool/request.json":
                raise ValueError("parser cited evidence not produced by the declared tool")
        ctx.result["details"]["producer"] = producer
        metrics = report["metrics"]
        if not isinstance(metrics, dict) or not metrics:
            raise ValueError("adapter returned no measurements")
        for name, metric in metrics.items():
            finite(metric["value"], name)
            for key in ("unit", "condition"):
                if not isinstance(metric[key], str) or not metric[key].strip():
                    raise ValueError(f"metric {name} needs {key}")
        finish(ctx, metrics, ctx.params["limits"])
    except (ValueError, KeyError, TypeError) as exc:
        raise CheckError("malformed", str(exc)) from exc


def execute_measurement_tool(ctx, request):
    from .checks import CheckError
    from .executor import execute

    tool = ctx.params["tool"]
    argv = tool["argv"]
    if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a for a in argv):
        raise ValueError("measurement tool requires explicit argv")
    executable = Path(argv[0])
    if not executable.is_absolute() or not executable.resolve().is_relative_to("/usr"):
        raise ValueError("measurement tool executable must reside under /usr")
    if digest(executable) != tool["sha256"]:
        raise ValueError("measurement tool executable differs from pinned baseline")
    if tool["domain"] not in ("electrical", "thermal", "parasitic", "manufacturing", "supply_chain"):
        raise ValueError("unsupported measurement tool domain")
    work = ctx.work / "tool"
    work.mkdir()
    write_json(work / "request.json", request)
    mounts = [(ctx.run_dir / "baseline.json", "/evidence/baseline.json")] if request["baseline"] else []
    mounts.append((work / "request.json", "/work/request.json"))
    remaining = ctx.remaining()
    if remaining <= 0:
        raise CheckError("interrupted", "deadline exhausted before measurement tool")
    execution = execute(ctx.inputs, work, argv, timeout=min(remaining, ctx.params.get("timeout_s", 600)),
                        extra_ro=mounts)
    hashes = {}
    for path in sorted(work.rglob("*")):
        if path.is_symlink():
            raise ValueError("measurement tool output contains a live link")
        if path.is_file():
            rel = path.relative_to(ctx.work).as_posix()
            hashes[rel] = digest(path)
            ctx.evidence(rel)
    if execution["execution"] != "ok":
        raise CheckError(execution["execution"], "declared measurement tool did not complete")
    if digest(executable) != tool["sha256"]:
        raise ValueError("measurement tool changed during execution")
    return {"tool": str(executable), "sha256": tool["sha256"], "domain": tool["domain"],
            "argv": argv, "execution": execution, "raw_sha256": hashes}, hashes


def check_coupled_margins(ctx):
    """Join margins from different domains at the same declared operating point.

    The intersection is conjunctive: a thermal improvement cannot compensate for
    a lost EMI or manufacturing margin. Physics calculations belong in adapters.
    """
    from .checks import CheckError

    try:
        members = ctx.params["members"]
        if not isinstance(members, list) or len(members) < 2:
            raise ValueError("coupling requires at least two measurements")
        metrics, domains, sources = {}, set(), {}
        for member in members:
            cid = member["check"]
            preceding = [s["id"] for s in ctx.scope["checks"]]
            if cid not in preceding[:preceding.index(ctx.cid)]:
                raise ValueError("coupling must reference an earlier check")
            check = read_json(ctx.run_dir / "checks" / f"{cid}.json")
            if check["candidate_id"] != ctx.manifest["candidate_id"] or check["execution"] != "ok":
                raise ValueError("coupled source is missing current successful execution")
            metric = check["details"]["metrics"][member["metric"]]
            if metric["condition"] != ctx.params["condition"]:
                raise ValueError("coupled measurements have different operating conditions")
            name = member["name"]
            if name in metrics:
                raise ValueError("duplicate coupling member name")
            metrics[name] = metric
            domains.add(check["details"]["producer"]["domain"])
            sources[cid] = check
        if len(domains) < 2:
            raise ValueError("coupling requires distinct physical domains")
        if set(metrics) != set(ctx.params["limits"]):
            raise ValueError("all coupled margins must have limits")
        write_json(ctx.work / "coupling.json", {"condition": ctx.params["condition"],
            "sources": sources, "domains": sorted(domains), "metrics": metrics})
        ctx.evidence("coupling.json")
        finish(ctx, metrics, ctx.params["limits"])
        ctx.result["details"]["domains"] = sorted(domains)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise CheckError("malformed", str(exc)) from exc


def describe_measurements(ctx):
    ctx.result['predicates'] = _measurements(ctx.params, ctx.captured)


if __name__ != '__main__':
    _register_check('measurements', describe_measurements, check_measurements)


def describe_coupled_margins(ctx):
    ctx.result['predicates'] = _coupled_margins(ctx.params, ctx.prerequisite, ctx.files, ctx.inputs)


if __name__ != '__main__':
    _register_check('coupled_margins', describe_coupled_margins, check_coupled_margins)


def _measurements(params, captured):
    from pathlib import PurePosixPath
    from .capabilities import DOMAINS, bounds
    predicates = []
    captured(params['adapter'], immutable=True)
    tool = params['tool']
    argv = tool['argv']
    if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a for a in argv):
        raise ValueError('tool requires explicit argv')
    executable = PurePosixPath(argv[0])
    if not executable.is_absolute() or '..' in executable.parts or not executable.is_relative_to('/usr'):
        raise ValueError('tool executable must reside under /usr')
    if not isinstance(tool['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', tool['sha256']):
        raise ValueError('tool needs pinned sha256')
    if tool['domain'] not in DOMAINS:
        raise ValueError('unsupported tool domain')
    inputs = tool.get('inputs', [])
    if not isinstance(inputs, list):
        raise ValueError('tool inputs must be a list')
    for path in inputs:
        captured(path, immutable=True)
    # Candidate program arguments must be owned immutable dependencies.
    for arg in argv:
        if arg.startswith('/candidate/') and arg[len('/candidate/'):] not in inputs:
            raise ValueError('candidate tool argument missing from immutable inputs')
    predicates = bounds(params['limits'])
    return predicates


def _coupled_margins(params, prerequisite, files, inputs):
    from .capabilities import bounds, text
    members = params['members']
    if not isinstance(members, list) or len(members) < 2:
        raise ValueError('coupling requires at least two members')
    condition = text(params['condition'], 'coupling condition')
    domains, names = set(), set()
    for member in members:
        source = prerequisite(member['check'], ('measurements', 'rc_transient', 'passive_corners', 'dc_path'))
        source_params = source.get('params', {})
        if source['kind'] == 'dc_path':
            from .dc_path import load_inputs, limits
            contract, _ = load_inputs(source_params, files, inputs)
            metric = limits(contract)[member['metric']]
            domain = 'electrical'
        elif source['kind'] == 'measurements':
            metric = source_params['limits'][member['metric']]
            domain = source_params['tool']['domain']
        else:
            m = next(m for m in source_params['measurements'] if m['name'].lower() == member['metric'])
            from .passive_corners import condition as corner_condition
            metric = {'unit': 'V', 'condition': (corner_condition(source_params, m)
                if source['kind'] == 'passive_corners' else
                f"ideal-unloaded-{source_params.get('step_v', 1.0)}V-step-at-{float(m['at_ms'])}ms")}
            domain = 'electrical'
        if metric['condition'] != condition:
            raise ValueError('coupled source condition mismatch')
        name = text(member['name'], 'member name')
        if name in names:
            raise ValueError('duplicate coupling name')
        names.add(name)
        domains.add(domain)
        target = params['limits'][name]
        if target['unit'] != metric['unit'] or target['condition'] != condition:
            raise ValueError('coupled limit identity mismatch')
    if len(domains) < 2 or names != set(params['limits']):
        raise ValueError('coupling needs distinct domains and complete limits')
    predicates = bounds(params['limits'])
    return predicates
