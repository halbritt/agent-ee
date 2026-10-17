"""Capture physical observations and propagate bounded model discrepancies."""

import copy
import shutil
from pathlib import Path

from .candidate import safe_relative
from .storage import digest, identity, read_json, write_json


def calibration_for(run, scope, files):
    path = scope.get("empirical")
    if path is None:
        return None
    path = str(safe_relative(path))
    if path not in files:
        raise ValueError("empirical calibration must be a captured input")
    bundle = read_json(Path(run) / "inputs" / path)
    if bundle.get("schema") != "agent-ee.calibration/1":
        raise ValueError("expected agent-ee.calibration/1")
    from .baseline import load_baseline
    baseline = load_baseline(run)
    if baseline is not None and bundle.get("baseline_id") != baseline["baseline_id"]:
        raise ValueError("empirical calibration belongs to a different baseline")
    for rel, sha in bundle["artifacts"].items():
        if files.get(rel) != sha:
            raise ValueError("empirical raw evidence changed or missing")
    for rel, sha in bundle["model_inputs"].items():
        if files.get(rel) != sha:
            raise ValueError("calibration model applicability changed; new bench evidence required")
    validate_observations(Path(run) / "inputs", bundle)
    return bundle


def validate_observations(inputs, bundle):
    from .measurements import finite

    observations = bundle["observations"]
    if not isinstance(observations, list) or not observations:
        raise ValueError("calibration requires retained observations")
    for observation in observations:
        for key in ("artifact", "source_result", "report"):
            rel = str(safe_relative(observation[key]))
            if rel not in bundle["artifacts"]:
                raise ValueError("calibration observation cites unretained provenance")
        index = int(observation["locator"])
        if index < 0 or str(index) != observation["locator"]:
            raise ValueError("invalid calibration sample locator")
        sample = read_json(inputs / observation["artifact"])["samples"][index]
        source = read_json(inputs / observation["source_result"])
        report = read_json(inputs / observation["report"])
        if source["candidate_id"] != observation["candidate_id"] or report["candidate_id"] != source["candidate_id"]:
            raise ValueError("calibration source candidate mismatch")
        check = next((c for c in source["checks"] if c["id"] == observation["check_id"]), None)
        if check is None or check["execution"] != "ok":
            raise ValueError("calibration lacks executed source prediction")
        metric = check["details"]["metrics"][observation["metric"]]
        prediction = finite(metric.get("nominal", metric["value"]), "prediction")
        measured = finite(sample["value"], "observed value")
        uncertainty = finite(sample["uncertainty"], "uncertainty")
        if uncertainty < 0 or any(sample[key] != observation[key] for key in
                ("check_id", "metric", "unit", "condition", "value", "uncertainty")):
            raise ValueError("calibration differs from captured bench sample")
        if (metric["unit"] != sample["unit"] or metric["condition"] != sample["condition"] or
                prediction != observation["prediction"] or
                measured - uncertainty - prediction != observation["residual_lower"] or
                measured + uncertainty - prediction != observation["residual_upper"]):
            raise ValueError("calibration residual does not reproduce captured measurements")


def calibrate(ctx, metrics):
    from .measurements import finite

    bundle = calibration_for(ctx.run_dir, ctx.scope, ctx.manifest["files"])
    if bundle is None:
        return metrics
    calibrated = copy.deepcopy(metrics)
    replay = []
    for observation in bundle["observations"]:
        if observation["check_id"] != ctx.cid:
            continue
        name = observation["metric"]
        metric = calibrated[name]
        if metric["unit"] != observation["unit"] or metric["condition"] != observation["condition"]:
            raise ValueError("bench calibration operating condition or unit changed")
        nominal = finite(metrics[name]["value"], name)
        lower = nominal + finite(observation["residual_lower"], "residual_lower")
        upper = nominal + finite(observation["residual_upper"], "residual_upper")
        if lower > upper:
            raise ValueError("reversed empirical uncertainty interval")
        if "nominal" in metric:
            lower, upper = min(lower, metric["lower"]), max(upper, metric["upper"])
        metric.update(nominal=nominal, lower=lower, upper=upper, value=(lower + upper) / 2)
        replay.append({"observation": observation, "prediction": nominal,
                       "calibrated_lower": lower, "calibrated_upper": upper})
    if replay:
        write_json(ctx.work / "bench-replay.json", {"source_candidate": bundle["candidate_id"],
            "cases": replay, "method": "additive discrepancy envelope; both bounds tested against unchanged limits"})
        ctx.evidence("bench-replay.json")
        ctx.result["details"]["empirical_cases"] = replay
    return calibrated


def ingest(run, report_path, out):
    """Produce an immutable-input bundle; no inference or external communication."""
    from .cli import verify
    from .baseline import load_baseline
    from .measurements import finite

    run, report_path, out = Path(run).resolve(), Path(report_path).resolve(), Path(out).resolve()
    result = verify(run, current_source=False)
    baseline = load_baseline(run)
    if baseline is None:
        raise ValueError("bench feedback requires a version-2 baseline")
    manifest = read_json(run / "candidate.json")
    report = read_json(report_path)
    if report.get("schema") != "agent-ee.bench/1" or report.get("candidate_id") != manifest["candidate_id"]:
        raise ValueError("bench report must identify the measured candidate")
    for key in ("prototype", "instrument", "instrument_calibration", "procedure", "observed_utc"):
        if not isinstance(report.get(key), str) or not report[key].strip():
            raise ValueError(f"bench report requires {key}")
    if not isinstance(report.get("observations"), list) or not report["observations"]:
        raise ValueError("bench report needs observations")
    raw = report.get("artifacts")
    if not isinstance(raw, list) or not raw:
        raise ValueError("bench report needs raw artifact paths and hashes")
    sources = {}
    for item in raw:
        rel = str(safe_relative(item["path"]))
        source = report_path.parent / rel
        if not source.resolve().is_relative_to(report_path.parent) or source.is_symlink():
            raise ValueError("bench artifact escapes report directory")
        if digest(source) != item["sha256"]:
            raise ValueError("bench raw artifact checksum mismatch")
        if rel in sources:
            raise ValueError("duplicate bench artifact")
        sources[rel] = source
    checks = {c["id"]: c for c in result["checks"]}
    specs = {c["id"]: c for c in manifest["scope"]["checks"]}
    observations, models = [], {}
    for item in report["observations"]:
        cid, name = item["check_id"], item["metric"]
        check = checks[cid]
        if check["execution"] != "ok" or specs[cid]["kind"] not in ("measurements", "rc_transient"):
            raise ValueError("bench observation requires an executed measurement adapter")
        metric = check["details"]["metrics"][name]
        prediction = finite(metric.get("nominal", metric["value"]), "nominal prediction")
        if metric["unit"] != item["unit"] or metric["condition"] != item["condition"]:
            raise ValueError("bench observation unit or operating condition mismatch")
        if item["artifact"] not in sources or not isinstance(item.get("locator"), str) or not item["locator"].strip():
            raise ValueError("bench observation must locate a raw measurement")
        raw_measurements = read_json(sources[item["artifact"]])
        try:
            index = int(item["locator"])
            if index < 0 or str(index) != item["locator"]:
                raise ValueError("sample index must be canonical and nonnegative")
            sample = raw_measurements["samples"][index]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ValueError("bench locator must identify a sample in captured raw JSON") from exc
        fields = ("check_id", "metric", "unit", "condition", "value", "uncertainty")
        if any(sample.get(key) != item[key] for key in fields):
            raise ValueError("bench observation differs from captured instrument sample")
        measured, uncertainty = finite(item["value"], "bench value"), finite(item["uncertainty"], "bench uncertainty")
        if uncertainty < 0:
            raise ValueError("bench uncertainty must be nonnegative")
        applicability = specs[cid]["params"].get("calibration_inputs")
        if not isinstance(applicability, list) or not applicability:
            raise ValueError("adapter must declare immutable calibration_inputs")
        adapter = specs[cid]["params"].get("adapter")
        for path in ([adapter] if adapter else []) + applicability + specs[cid]["params"].get("tool", {}).get("inputs", []):
            models[path] = manifest["files"][path]
        observations.append({**item, "prediction": prediction,
            "residual_lower": measured - uncertainty - prediction,
            "residual_upper": measured + uncertainty - prediction})
    out.mkdir(parents=True, exist_ok=False)
    hashes, previous_observations, previous_models = merge_previous(run, manifest, result, baseline, out)
    raw_paths = {}
    for rel, source in sources.items():
        raw_paths[rel] = retain_artifact(source, out, hashes)
        if hashes[raw_paths[rel]] != next(a["sha256"] for a in raw if a["path"] == rel):
            raise ValueError("bench artifact changed during capture")
    report_copy = out / "report.json"
    write_json(report_copy, report)
    report_ref = retain_artifact(report_copy, out, hashes)
    report_copy.unlink()
    prediction_copy = out / "predictions.json"
    # Initial reviewers may inspect empirical inputs. Retain tool facts only;
    # prior reviewer judgments and adjudication prose must not cross epochs.
    write_json(prediction_copy, {"candidate_id": manifest["candidate_id"],
        "checks": result["checks"], "source_result_sha256": digest(run / "result.json")})
    result_ref = retain_artifact(prediction_copy, out, hashes)
    prediction_copy.unlink()
    for observation in observations:
        observation.update(artifact=raw_paths[observation["artifact"]], report=report_ref,
                           source_result=result_ref, candidate_id=manifest["candidate_id"])
    for rel, sha in previous_models.items():
        if manifest["files"].get(rel) != sha or (rel in models and models[rel] != sha):
            raise ValueError("prior empirical model applicability changed")
    models.update(previous_models)
    unique = {identity(o): o for o in [*previous_observations, *observations]}
    bundle = {"schema": "agent-ee.calibration/1", "candidate_id": manifest["candidate_id"],
        "baseline_id": baseline["baseline_id"], "source_result_sha256": digest(run / "result.json"),
        "model_inputs": models, "artifacts": hashes, "observations": list(unique.values())}
    write_json(out / "calibration.json", bundle)
    return bundle


def retain_artifact(source, out, hashes):
    sha = digest(source)
    rel = f"empirical/artifacts/{sha}"
    target = out / "artifacts" / sha
    target.parent.mkdir(exist_ok=True)
    if not target.exists():
        shutil.copyfile(source, target)
    if digest(target) != sha:
        raise ValueError("empirical evidence changed during capture")
    hashes[rel] = sha
    return rel


def merge_previous(run, manifest, result, baseline, out):
    bundles = []
    if manifest["scope"].get("empirical"):
        bundle = calibration_for(run, manifest["scope"], manifest["files"])
        bundles.append((bundle, run / "inputs/empirical"))
    for event in result.get("empirical_feedback", []):
        root = Path(event["bundle"])
        if digest(root / "calibration.json") != event["bundle_sha256"]:
            raise ValueError("prior bench feedback bundle changed")
        bundles.append((read_json(root / "calibration.json"), root))
    hashes, observations, models = {}, [], {}
    for bundle, root in bundles:
        if bundle["baseline_id"] != baseline["baseline_id"]:
            raise ValueError("prior bench feedback belongs to another baseline")
        for rel, sha in bundle["artifacts"].items():
            relative = safe_relative(rel)
            if relative.parts[0] != "empirical":
                raise ValueError("prior empirical artifact has an invalid path")
            source = root.joinpath(*relative.parts[1:])
            if digest(source) != sha or retain_artifact(source, out, hashes) != rel:
                raise ValueError("prior empirical artifact is not content-addressed")
        for rel, sha in bundle["model_inputs"].items():
            if rel in models and models[rel] != sha:
                raise ValueError("conflicting empirical model identities")
            models[rel] = sha
        observations.extend(bundle["observations"])
    return hashes, observations, models


def bench_review(run, report, out, max_seconds, *, review_out=None):
    from .cli import run_review, save_result, verify
    from .locking import writer_lock

    run, out = Path(run).resolve(), Path(out).resolve()
    review_out = Path(review_out).resolve() if review_out is not None else out / "review"
    with writer_lock(run, out):
        out.mkdir(parents=True, exist_ok=False)
        ingest(run, report, out / "empirical")
        # A newly captured physical discrepancy invalidates current acceptance
        # even if re-verification is interrupted or the provider is unavailable.
        original = verify(run, current_source=False)
        event = {"schema": "agent-ee.empirical-feedback/1", "bundle": str(out / "empirical"),
                 "bundle_sha256": digest(out / "empirical/calibration.json"), "review": str(review_out)}
        write_json(run / "events" / ("bench-" + event["bundle_sha256"] + ".json"), event)
        original.setdefault("empirical_feedback", []).append(event)
        save_result(run, original)
        manifest = read_json(run / "candidate.json")
        scope = copy.deepcopy(manifest["scope"])
        scope["inputs"] = [{"path": str(p), "at": p.name} for p in sorted((run / "inputs").iterdir()) if p.name != "empirical"]
        scope["inputs"].append({"path": str(out / "empirical"), "at": "empirical"})
        scope["empirical"] = "empirical/calibration.json"
        write_json(out / "scope.json", scope)
        return run_review(out / "scope.json", review_out, max_seconds, previous=str(run))
