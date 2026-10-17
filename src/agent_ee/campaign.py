"""One implementation writer, immutable candidates, independent fresh reviews."""

import copy
import json
import shutil
import time
from pathlib import Path

from .baseline import load_baseline
from .convergence import advance
from .locking import writer_lock
from .profiles import validate_profile
from .storage import digest, read_json, write_json

IMPLEMENTATION_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["summary"], "properties": {"summary": {"type": "string"}}}


def implement(root, previous, definition, state, deadline):
    from .reviewers import reviewer_call

    manifest = read_json(previous / "candidate.json")
    work = root / "implementations" / str(len(state["history"]))
    work.mkdir(parents=True)
    shutil.copytree(previous / "inputs", work / "inputs")
    shutil.copyfile(previous / "baseline.json", work / "baseline.json")
    design = work / "review-work/implementation/design"
    shutil.copytree(previous / "inputs", design)
    prompt = ("You are the sole implementation writer for this electronics campaign. "
        "Edit only /work/design, the disposable copy of /candidate. Preserve all immutable baseline sources "
        "and verification criteria. Repair the adjudicated defects; preserve every previously passing criterion. "
        "Requirements are at /evidence/baseline.json and their full source paths under /candidate. "
        "Do not change tests, adapters, requirements, target specifications or reference architectures. "
        "Return a JSON summary. Only captured file changes count; do not claim checks passed without evidence.\n" +
        json.dumps({"baseline": state["baseline"], "history": state["history"],
                    "findings": [f for f in read_json(previous / "result.json")["findings"]
                                 if f.get("source") == "check" and f.get("status") == "accepted"]}))
    record, response = reviewer_call(work, "implementation", {**definition, "tool_mode": "investigate"},
        prompt, IMPLEMENTATION_SCHEMA, min(definition["timeout_s"], max(.001, deadline - time.monotonic())))
    write_json(work / "implementation-execution.json", record)
    if record["execution"] != "ok" or response is None:
        raise ValueError("implementation execution failed; no retry or provider fallback")
    actual = {}
    for path in design.rglob("*"):
        if path.is_symlink():
            raise ValueError("implementation introduced a live link")
        if path.is_file():
            actual[path.relative_to(design).as_posix()] = digest(path)
    if actual.keys() != manifest["files"].keys():
        raise ValueError("implementation changed the captured file inventory")
    for rel, sha in manifest["files"].items():
        if rel not in state["mutable_inputs"] and actual[rel] != sha:
            raise ValueError(f"implementation changed immutable verification input: {rel}")
    for rel, sha in state["baseline"]["files"].items():
        if not (design / rel).is_file() or digest(design / rel) != sha:
            raise ValueError(f"implementation changed immutable baseline source: {rel}")
    # Tool programs are part of the frozen verification harness, never repair targets.
    for spec in manifest["scope"]["checks"]:
        adapter = spec.get("params", {}).get("adapter")
        if adapter and (not (design / adapter).is_file() or digest(design / adapter) != manifest["files"][adapter]):
            raise ValueError("implementation changed a verification adapter")
    scope = copy.deepcopy(manifest["scope"])
    scope["inputs"] = [{"path": str(p), "at": p.name} for p in sorted(design.iterdir())]
    path = work / "proposal.scope.json"
    write_json(path, scope)
    return path


def run_campaign(config_path, out):
    from .cli import run_review, verify
    from .measurements import finite

    config_path = Path(config_path).resolve()
    config = read_json(config_path)
    if config.get("schema") != "agent-ee.campaign/1":
        raise ValueError("expected agent-ee.campaign/1")
    iterations = config["max_iterations"]
    if isinstance(iterations, bool) or not isinstance(iterations, int) or not 1 <= iterations <= 100:
        raise ValueError("max_iterations must be between 1 and 100")
    seconds = finite(config["max_seconds"], "max_seconds")
    if not 1 <= seconds <= 86400:
        raise ValueError("max_seconds must be between 1 and 86400")
    scope_path = (config_path.parent / config["scope"]).resolve()
    scope = read_json(scope_path)
    if scope.get("schema") not in ("agent-ee.scope/2", "agent-ee.scope/3"):
        raise ValueError("campaign requires an immutable scope/2 or scope/3 baseline")
    profile = validate_profile(config["review_profile"])
    # Reuse the subscription route validator for the implementation definition.
    validate_profile({**profile, "adjudicator": config["implementation"]})
    root = Path(out).resolve()
    with writer_lock(root):
        root.mkdir(parents=True, exist_ok=False)
        (root / ".campaign-root").touch()
        write_json(root / "config.json", config)
        write_json(root / "profile.json", profile)
        state = {"schema": "agent-ee.campaign-state/1", "state": "running", "history": [],
                 "config_sha256": digest(root / "config.json")}
        write_json(root / "state.json", state)
        return continue_campaign(root, state, scope_path, time.monotonic() + seconds)


def record_iteration(root, state, run, config):
    from .cli import verify

    result = verify(run)
    manifest = read_json(run / "candidate.json")
    baseline = load_baseline(run)
    if "baseline" not in state:
        state["baseline"] = baseline
        state["mutable_inputs"] = mutable_inputs(manifest, baseline)
    elif state["baseline"] != baseline:
        raise ValueError("candidate changed the campaign baseline")
    epoch = state.get("epoch", 0)
    comparable = [h for h in state["history"] if h["epoch"] == epoch]
    budget = config["max_iterations"] - (len(state["history"]) - len(comparable))
    record, diagnostic = advance(comparable, result, manifest, budget)
    snapshot = root / "history" / f"{len(state['history']) + 1}.json"
    snapshot.parent.mkdir(exist_ok=True)
    shutil.copyfile(run / "result.json", snapshot)
    empirical = manifest["scope"].get("empirical")
    record.update(run=str(run), result_sha256=digest(snapshot), result_snapshot=str(snapshot), epoch=epoch,
                  calibration_id=manifest["files"].get(empirical) if empirical else None)
    state["history"].append(record)
    state.update({k: v for k, v in diagnostic.items() if k not in ("schema", "history")})
    state["diagnostic"] = diagnostic
    if state["state"] == "escalated":
        write_json(root / "tradeoff.json", diagnostic)
    write_json(root / "state.json", state)


def mutable_inputs(manifest, baseline):
    scope = manifest["scope"]
    mutable = scope.get("mutable_inputs", [scope["board"], scope["schematic"]])
    if (not isinstance(mutable, list) or not mutable or
            any(not isinstance(p, str) or p not in manifest["files"] for p in mutable) or
            set(mutable) & baseline["files"].keys()):
        raise ValueError("mutable_inputs must name captured design files outside the immutable baseline")
    return mutable


def continue_campaign(root, state, scope_path, deadline, *, initial_run=None):
    from .cli import run_review, verify

    config = read_json(root / "config.json")
    try:
        while True:
            run = initial_run or root / "runs" / str(len(state["history"]) + 1)
            if initial_run is None:
                if len(state["history"]) >= config["max_iterations"] or time.monotonic() >= deadline:
                    raise ValueError("campaign iteration or wall-clock budget exhausted")
                run_review(scope_path, run, deadline - time.monotonic(), profile=root / "profile.json",
                           expected_baseline=state.get("baseline"))
            initial_run = None
            record_iteration(root, state, run, config)
            verify_campaign(root)
            if state["state"] != "repair":
                return 0 if state["state"] == "criteria_satisfied" else 1
            with writer_lock(run):
                verify(run)
                scope_path = implement(root, run, config["implementation"], state, deadline)
    except (ValueError, OSError, KeyError, TypeError, KeyboardInterrupt) as exc:
        state.update(state="incomplete", error=f"{type(exc).__name__}: {exc}")
        write_json(root / "state.json", state)
        return 1


def verify_campaign(root):
    root = Path(root).resolve()
    with writer_lock(root):
        return verify_campaign_records(root)


def verify_campaign_records(root):
    """Existing integrity decisions; caller establishes coherent reads if needed."""
    from .cli import verify

    root = Path(root).resolve()
    state = read_json(root / "state.json")
    config = read_json(root / "config.json")
    if digest(root / "config.json") != state["config_sha256"]:
        raise ValueError("campaign configuration changed")
    if read_json(root / "profile.json") != config["review_profile"]:
        raise ValueError("campaign profile changed")
    history = []
    diagnostic = {"state": "running"}
    feedback_seen = False
    for index, stored in enumerate(state["history"], 1):
        run = root / "runs" / str(index)
        snapshot = root / "history" / f"{index}.json"
        if (str(run) != stored["run"] or str(snapshot) != stored["result_snapshot"] or
                digest(snapshot) != stored["result_sha256"]):
            raise ValueError("campaign history result link changed")
        current = verify(run, current_source=False)
        result = read_json(snapshot)
        if current != result:
            # Bench registration appends facts without rewriting the original
            # judgment. Arbitrary post-hoc adjudication is not a feedback epoch.
            allowed = {"empirical_feedback", "artifact_hashes", "outcome"}
            if (not current.get("empirical_feedback") or
                    {k: v for k, v in current.items() if k not in allowed} !=
                    {k: v for k, v in result.items() if k not in allowed}):
                raise ValueError("campaign history result link changed")
            if index == len(state["history"]):
                feedback_seen = True
        manifest = read_json(run / "candidate.json")
        if load_baseline(run) != state["baseline"]:
            raise ValueError("campaign history baseline changed")
        empirical = manifest["scope"].get("empirical")
        calibration_id = manifest["files"].get(empirical) if empirical else None
        if stored["calibration_id"] != calibration_id:
            raise ValueError("campaign epoch calibration identity changed")
        if not history:
            if state["mutable_inputs"] != mutable_inputs(manifest, state["baseline"]):
                raise ValueError("campaign mutable_inputs differ from captured write authority")
            if stored["epoch"] != 0:
                raise ValueError("campaign must begin at epoch zero")
        elif stored["epoch"] != history[-1]["epoch"]:
            previous = history[-1]
            previous_result = read_json(Path(previous["run"]) / "result.json")
            transitions = previous_result.get("empirical_feedback", [])
            if (stored["epoch"] != previous["epoch"] + 1 or calibration_id == previous["calibration_id"] or
                    not any(e["bundle_sha256"] == calibration_id and e["review"] == str(run) for e in transitions)):
                raise ValueError("campaign epoch lacks a bound physical feedback transition")
        elif history[-1]["calibration_id"] != calibration_id:
            raise ValueError("calibration changed without an empirical epoch transition")
        comparable = [h for h in history if h["epoch"] == stored["epoch"]]
        budget = config["max_iterations"] - (len(history) - len(comparable))
        record, diagnostic = advance(comparable, result, manifest, budget)
        metadata = {"run", "result_sha256", "result_snapshot", "epoch", "calibration_id"}
        if record != {k: v for k, v in stored.items() if k not in metadata}:
            raise ValueError("campaign history projection differs from evidence")
        history.append(stored)
    if state["state"] != diagnostic["state"] and not (state["state"] == "incomplete" and state.get("error")):
        raise ValueError("campaign terminal state contradicts convergence history")
    return {**state, "state": "empirical_superseded"} if feedback_seen else state


def campaign_feedback(root, report, out, max_seconds):
    from .empirical import bench_review
    from .measurements import finite

    root, out = Path(root).resolve(), Path(out).resolve()
    seconds = finite(max_seconds, "max_seconds")
    if not 1 <= seconds <= 86400:
        raise ValueError("feedback max_seconds must be between 1 and 86400")
    with writer_lock(root, out):
        checked = verify_campaign(root)
        state = read_json(root / "state.json")
        if not state["history"] or checked["state"] not in ("criteria_satisfied", "empirical_superseded"):
            raise ValueError("bench continuation requires a completed accepted candidate or pending physical feedback")
        run = Path(state["history"][-1]["run"])
        next_run = root / "runs" / str(len(state["history"]) + 1)
        deadline = time.monotonic() + seconds
        try:
            bench_review(run, report, out, seconds, review_out=next_run)
            state["epoch"] = state.get("epoch", 0) + 1
            state.pop("error", None)
            # A new physical observation changes model knowledge, not requirements.
            # Old epochs stay visible to implementation and forensic verification.
            return continue_campaign(root, state, out / "scope.json", deadline, initial_run=next_run)
        except (ValueError, OSError, KeyError, TypeError, KeyboardInterrupt) as exc:
            state.update(state="incomplete", error=f"{type(exc).__name__}: {exc}")
            write_json(root / "state.json", state)
            return 1
