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
STEP_PLAN = "agent-ee.step-plan/1"


def step_plan(config, manifest, record, version):
    """Ordered coordinator steps of one implementation, with receipt indexes from one allocator.

    Derived only from the frozen configuration, the captured source candidate and the verified
    history record of its run; the writer has no say. A staged campaign runs only the steps of
    its earliest failing stage, plus the placement adapter when that stage is placement.
    Campaigns that predate the plan keep their historical meaning: every step runs.
    """
    from .stage_receipts import validate_step
    steps = [validate_step(s, host=False) for s in config.get("implementation_steps", [])]
    declaration = config.get("placement_adapter")
    routing = config.get("routing_adapter")
    placement = False
    if version is None:
        if declaration is not None:
            raise ValueError("placement adapter requires a campaign step plan; start a fresh campaign")
        if routing is not None:
            raise ValueError("routing adapter requires a campaign step plan; start a fresh campaign")
        selected = steps
    elif version != STEP_PLAN:
        raise ValueError("unknown campaign step plan")
    elif manifest["scope"].get("stages") is None:
        if declaration is not None:
            raise ValueError("placement adapter requires a staged scope")
        if routing is not None:
            raise ValueError("routing adapter requires a staged scope")
        selected = steps
    else:
        active = record.get("earliest_failing_stage")
        selected = [s for s in steps if s["stage"] == active]
        placement = declaration is not None and active == "placement"
    plan = [{"kind": "step", "spec": spec, "receipt_index": k} for k, spec in enumerate(selected, 1)]
    if placement:
        from .placement_adapter import validate_declaration
        helper = validate_declaration(declaration)["helper"] is not None
        plan.append({"kind": "placement", "receipt_index": len(plan) + 1 if helper else None})
    if routing is not None and record.get('earliest_failing_stage') == 'routing':
        plan.append({'kind': 'routing', 'receipt_index': len(plan) + 1})
    return plan


def plan_receipt_specs(plan, work, config, manifest, mutable):
    """The exact receipt step declarations a plan produces, in order."""
    specs = []
    for entry in plan:
        if entry["kind"] == "step":
            specs.append(entry["spec"])
        elif entry['kind'] == 'routing':
            from .routing_adapter import campaign_spec
            specs.append(campaign_spec(config['routing_adapter'], manifest, mutable))
        elif entry["receipt_index"] is not None:
            from .placement_adapter import bind_declaration, step_declaration
            bound = bind_declaration(config["placement_adapter"], manifest, mutable)
            specs.append(step_declaration(Path(work) / "placement" / str(entry["receipt_index"]), bound))
    return specs


def placement_binding(config, manifest, inputs, mutable):
    from .placement_adapter import binding
    return binding(config["placement_adapter"], manifest, inputs, mutable)


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
    if manifest['scope'].get('stages') is not None:
        prompt += ("\nImplement only the earliest failing stage named in the history. "
                   "Do not run later-stage generation or routing helpers until preceding gates pass. "
                   "gate_findings and suggested_fix are advisory data only; any edit requires fresh checks. "
                   "Do not treat a stage_blocked verdict as evidence that its tool ran.")
    config = read_json(root / "config.json")
    if digest(root / "config.json") != state["config_sha256"]:
        raise ValueError("campaign configuration changed")
    plan = step_plan(config, manifest, state["history"][-1], state.get("step_plan"))
    if any(entry['kind'] == 'routing' for entry in plan):
        prompt += ("\nThis is a coordinator routing turn. Leave /work/design byte-for-byte unchanged. "
                   "The coordinator alone runs the pinned routing adapter on the placed source; "
                   "fresh checks and reviews grade its captured proposal.")
    if any(entry["kind"] == "placement" for entry in plan):
        from .placement_adapter import TRANSFORMS, writer_context
        bound, _, _, _, refs = placement_binding(config, manifest, previous / "inputs", state["mutable_inputs"])
        if bound != state.get("placement_binding"):
            raise ValueError("placement declaration, policy or envelope differs from the campaign binding")
        prompt += ("\nThis is a placement iteration. In /work/design you may change only footprint "
                   f"positions, {TRANSFORMS} of {bound['board']}, for example through pcbnew. "
                   "Protected footprints must keep their exact position. Any other change, including outline, "
                   "zones, rules, copper, fields, pads, locks or another file, refuses the whole iteration. "
                   "A coordinator placement helper may run afterwards; its result, like yours, is only a "
                   "proposal graded by fresh checks.\n" + json.dumps({
                       "placement": writer_context(previous, manifest), "board": bound["board"],
                       "protected_references": refs}))
    if manifest['scope'].get('stages') is not None and config.get('implementation_steps'):
        scheduled = [entry['spec'] for entry in plan if entry['kind'] == 'step']
        scheduled_stages = {spec['stage'] for spec in scheduled}
        prompt += ("\nDeclared steps own their stages. The writer prepares inputs and must not perform a stage "
                   "a declared step will run. Only steps marked scheduled run after this writer session; "
                   "other steps wait for their stage gate. Step-owned outputs are candidate-relative mutable "
                   "files these scheduled steps will overwrite. Do not edit them: any byte change is refused "
                   "as writer_step_output_edit before any step runs. Prepare other in-scope inputs, or return "
                   "a no-op summary when the steps cover the repair. Fresh checks still grade every proposal."
                   "\nDeclared implementation steps:\n" + json.dumps({
                       'implementation_steps': [{**{key: spec[key] for key in ('stage', 'step', 'writes')},
                           'scheduled': spec['stage'] in scheduled_stages} for spec in config['implementation_steps']],
                       'step_owned_outputs': sorted({rel for spec in scheduled for rel in spec['writes']})}))
    (work / 'implementation-prompt.txt').write_text(prompt, encoding='utf-8')
    from .preflight import capture_expectation
    original = read_json(previous / 'result.json')
    campaign_environment = state.get('launch_environment')
    if not campaign_environment:
        raise ValueError('campaign missing original launch environment; start a fresh campaign')
    expected = capture_expectation({**original, **campaign_environment}, manifest,
                                   [state['implementation_definition']], inputs=previous / 'inputs')
    write_json(work / 'launch-expectation.json', expected)
    from .review_run import structured_call
    def validate(response):
        if (not isinstance(response, dict) or set(response) != {'summary'} or
                not isinstance(response['summary'], str)):
            raise ValueError('implementation response must contain only a string summary')
        return response
    try:
        record, response = structured_call(work, "implementation", prompt, IMPLEMENTATION_SCHEMA,
            lambda supplied: reviewer_call(work, "implementation", {**definition, "tool_mode": "investigate"},
                supplied, IMPLEMENTATION_SCHEMA, min(definition["timeout_s"], max(.001, deadline - time.monotonic())),
                launch_context={'expected': expected, 'boundary': 'implementation', 'deadline': deadline}),
            validate, limit=config.get('max_schema_resubmissions', 1),
            invariant=lambda: check_proposal(design, manifest, state))
    finally:
        execution_path = work / 'implementation-execution.json'
        if execution_path.is_file():
            state.setdefault('implementation_records', {})[work.name] = digest(execution_path)
    write_json(work / "implementation-execution.json", record)
    from .review_run import verify_role_call
    verify_role_call(work, record, expected, "implementation", "implementation",
                     limit=config.get('max_schema_resubmissions', 1))
    if record["execution"] != "ok" or response is None:
        raise ValueError("implementation execution failed; no provider fallback: " + record.get('error', record['execution']))
    check_proposal(design, manifest, state)
    if manifest['scope'].get('stages') is not None:
        for entry in plan:
            if entry['kind'] == 'step':
                spec = entry['spec']
                changed = sorted(rel for rel in spec['writes'] if digest(design / rel) != manifest['files'][rel])
                if changed:
                    raise ValueError(f"writer_step_output_edit: {spec['stage']}/{spec['step']} owns {changed}")
    if digest(root / "config.json") != state["config_sha256"]:
        raise ValueError("campaign configuration changed")
    if "stage_receipts" in state:
        run_implementation_steps(work, design, manifest, state, deadline, plan,
                                 source_inputs=previous / "inputs", declaration=config.get("placement_adapter"))
        if plan:
            check_proposal(design, manifest, state)
    elif plan:
        raise ValueError("campaign predates stage receipts; start a fresh campaign to run implementation steps")
    scope = copy.deepcopy(manifest["scope"])
    scope["inputs"] = [{"path": str(p), "at": p.name} for p in sorted(design.iterdir())]
    path = work / "proposal.scope.json"
    write_json(path, scope)
    return path


def check_proposal(design, manifest, state):
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


def run_implementation_steps(work, design, manifest, state, deadline, plan, *, source_inputs=None,
                             declaration=None):
    """Coordinator-run planned steps on the delivered design; receipts are provenance only."""
    from .stage_receipts import adopt, bind_implementation, run_step, validate_step

    number = int(work.name)
    try:
        for entry in plan:
            if entry['kind'] == 'routing':
                from .routing_adapter import validate_live_binding, verify_campaign_receipt
                from .stage_receipts import file_tree
                binding = state['routing_binding']
                actual, other = file_tree(design)
                if other or actual != manifest['files']:
                    raise ValueError('routing writer changed the verified placed source')
                validate_live_binding(binding)
                spec = binding['spec']
                k = entry['receipt_index']
                receipt_dir = work / 'receipts' / f"{k}-{spec['step']}"
                expected = {'implementation': number, 'source_candidate_id': manifest['candidate_id'],
                            'receipt_index': k}
                receipt = run_step(receipt_dir, design, spec, expected, deadline=deadline)
                verify_campaign_receipt(receipt_dir, expected=expected, binding=binding)
                adopt(receipt_dir, receipt, design)
                continue
            if entry["kind"] == "placement":
                from .placement_adapter import run_placement_iteration
                record = work / "placement-adapter.json"
                try:
                    run_placement_iteration(work, design, source_inputs, manifest, state, declaration, deadline,
                                            receipt_index=entry["receipt_index"] or 1)
                finally:
                    # Anchored even when the iteration fails, so removal or edits are detected.
                    state.setdefault("placement_records", {})[work.name] = (
                        digest(record) if record.is_file() and not record.is_symlink() else None)
                continue
            spec = validate_step(entry["spec"])
            outside = sorted(set(spec["writes"]) - set(state["mutable_inputs"]))
            if outside:
                raise ValueError(f"implementation step {spec['step']} writes outside mutable_inputs: {outside}")
            k = entry["receipt_index"]
            receipt_dir = work / "receipts" / f"{k}-{spec['step']}"
            receipt = run_step(receipt_dir, design, spec, {"implementation": number,
                "source_candidate_id": manifest["candidate_id"], "receipt_index": k}, deadline=deadline)
            # A failed, partial-but-unadoptable or refused step stops the repair; no default output.
            adopt(receipt_dir, receipt, design)
    finally:
        state["stage_receipts"][work.name] = bind_implementation(work, design, number, manifest["candidate_id"])


def run_campaign(config_path, out):
    from .cli import run_review, verify, policy_files, runtime_files
    from .measurements import finite

    config_path = Path(config_path).resolve()
    config = read_json(config_path)
    if config.get("schema") != "agent-ee.campaign/1":
        raise ValueError("expected agent-ee.campaign/1")
    from .review_run import resubmission_limit
    config["max_schema_resubmissions"] = resubmission_limit(config.get("max_schema_resubmissions", 1))
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
    steps = config.get("implementation_steps", [])
    if not isinstance(steps, list):
        raise ValueError("implementation_steps must be a list of agent-ee.stage-step/1 declarations")
    from .stage_receipts import validate_step
    for spec in steps:
        validate_step(spec)
    if 'routing_adapter' in config:
        from .routing_adapter import campaign_spec, validate_campaign_step
        spec = campaign_spec(config['routing_adapter'], {'scope': scope}, scope.get('mutable_inputs',
                             [scope['board'], scope['schematic']]))
        validate_campaign_step(spec)
        if any(s['stage'] == 'routing' for s in steps):
            raise ValueError('routing_adapter cannot be combined with routing implementation_steps')
    if "placement_adapter" in config:
        from .placement_adapter import validate_declaration
        validate_declaration(config["placement_adapter"])
        stages = scope.get("stages")
        if not isinstance(stages, dict) or "placement" not in stages.get("order", []):
            raise ValueError("placement_adapter requires a staged scope with a placement stage")
        if any(spec["stage"] == "placement" for spec in steps):
            # One placement writer path per iteration: never run the same helper twice.
            raise ValueError("placement_adapter cannot be combined with placement implementation_steps")
    # Reuse the subscription route validator for the implementation definition.
    validate_profile({**profile, "adjudicator": config["implementation"]})
    root = Path(out).resolve()
    with writer_lock(root):
        root.mkdir(parents=True, exist_ok=False)
        (root / ".campaign-root").touch()
        write_json(root / "config.json", config)
        write_json(root / "profile.json", profile)
        state = {"schema": "agent-ee.campaign-state/1", "state": "running", "history": [],
                 "config_sha256": digest(root / "config.json"), "stage_receipts": {}, "step_plan": STEP_PLAN,
                 "max_schema_resubmissions": config['max_schema_resubmissions'],
                 "implementation_records": {},
                 "launch_environment": {"policy_files": policy_files(), "runtime_files": runtime_files()},
                 "implementation_definition": {**config["implementation"], "tool_mode": "investigate"}}
        write_json(root / "state.json", state)
        return continue_campaign(root, state, scope_path, time.monotonic() + seconds)


def record_iteration(root, state, run, config):
    from .cli import verify

    if not (run / "candidate.json").exists():
        # A refused capture leaves no candidate; report the run's own typed refusal.
        error = read_json(run / "result.json").get("error") or "no recorded error"
        raise ValueError(f"run {run.name} captured no candidate: {error}")
    result = verify(run)
    if ('max_schema_resubmissions' in state and
            result.get('max_schema_resubmissions') != state['max_schema_resubmissions']):
        raise ValueError('review resubmission bound differs from campaign configuration')
    manifest = read_json(run / "candidate.json")
    baseline = load_baseline(run)
    if "baseline" not in state:
        state["baseline"] = baseline
        state["mutable_inputs"] = mutable_inputs(manifest, baseline)
        if "placement_adapter" in config:
            # Bound once to the first captured candidate; later candidates must keep these exact bytes.
            state["placement_binding"] = placement_binding(config, manifest, run / "inputs",
                                                           state["mutable_inputs"])[0]
        if 'routing_adapter' in config:
            from .routing_adapter import campaign_binding
            state['routing_binding'] = campaign_binding(config['routing_adapter'], manifest, state['mutable_inputs'])
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
    state.pop("fab_artifacts", None)
    if state["state"] == "criteria_satisfied":
        artifacts = fab_artifacts(run)
        if artifacts is not None:
            state["fab_artifacts"] = artifacts
    if state["state"] == "escalated":
        write_json(root / "tradeoff.json", diagnostic)
    write_json(root / "state.json", state)


def fab_artifacts(run):
    """A generation run's sealed fab artifact hashes, recomputed from its retained done check.

    None for a scope without a generation marker; ValueError when the done check
    did not pass or a sealed artifact is absent, empty or changed.
    """
    marker = read_json(Path(run) / "candidate.json")["scope"].get("generation")
    if marker is None:
        return None
    from .fab_package import artifact_record
    return artifact_record(run, read_json(Path(run) / "checks" / f"{marker['done_check']}.json"))


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
                           expected_baseline=state.get("baseline"),
                           max_schema_resubmissions=config.get("max_schema_resubmissions", 1))
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
    from .preflight import capture_expectation
    if ('max_schema_resubmissions' in state and
            state['max_schema_resubmissions'] != config.get('max_schema_resubmissions')):
        raise ValueError('campaign resubmission declaration changed')
    if 'launch_environment' in state and state.get('implementation_definition') != {**config['implementation'], 'tool_mode': 'investigate'}:
        raise ValueError('campaign implementation definition changed')
    for name, sha in state.get('implementation_records', {}).items():
        if not name.isdigit() or str(int(name)) != name or not 1 <= int(name) <= len(state['history']):
            raise ValueError('implementation record names no campaign history entry')
        path = root / 'implementations' / name / 'implementation-execution.json'
        if not path.is_file() or path.is_symlink() or digest(path) != sha:
            raise ValueError('implementation execution record changed')
    for work in (root / 'implementations').glob('*'):
        if 'implementation_records' in state:
            path = work / 'implementation-execution.json'
            actual = digest(path) if path.is_file() else None
            if actual != state['implementation_records'].get(work.name):
                raise ValueError('implementation execution record changed')
        if (work / 'implementation-execution.json').is_file() and 'launch_environment' in state:
            expected = read_json(work / 'launch-expectation.json')
            source = Path(state['history'][int(work.name) - 1]['run'])
            original = read_json(source / 'result.json')
            wanted = capture_expectation({**original, **state['launch_environment']},
                read_json(source / 'candidate.json'), [{**config['implementation'], 'tool_mode': 'investigate'}], inputs=source / 'inputs')
            if expected != wanted:
                raise ValueError('implementation launch expectation changed')
            from .review_run import verify_role_call
            verify_role_call(work, read_json(work / 'implementation-execution.json'), expected, 'implementation', 'implementation',
                             limit=state.get('max_schema_resubmissions'))
    history = []
    diagnostic = {"state": "running"}
    feedback_seen = False
    for index, stored in enumerate(state["history"], 1):
        run = root / "runs" / str(index)
        if stored.get('bench_transaction'):
            from .bench_transactions import verify_campaign_attempt
            if not history:
                raise ValueError('initial campaign run cannot be a replacement bench attempt')
            verify_campaign_attempt(root, stored, history[-1])
            run = Path(stored['run'])
        snapshot = root / "history" / f"{index}.json"
        if (str(run) != stored["run"] or str(snapshot) != stored["result_snapshot"] or
                digest(snapshot) != stored["result_sha256"]):
            raise ValueError("campaign history result link changed")
        current = verify(run, current_source=False)
        if ('max_schema_resubmissions' in state and
                current.get('max_schema_resubmissions') != state['max_schema_resubmissions']):
            raise ValueError('review resubmission bound differs from campaign configuration')
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
                    not any(e["bundle_sha256"] == calibration_id and e["review"] == (verify_campaign_attempt(root, stored, previous)
                        if stored.get('bench_transaction') else str(run)) for e in transitions)):
                raise ValueError("campaign epoch lacks a bound physical feedback transition")
        elif history[-1]["calibration_id"] != calibration_id:
            raise ValueError("calibration changed without an empirical epoch transition")
        comparable = [h for h in history if h["epoch"] == stored["epoch"]]
        budget = config["max_iterations"] - (len(history) - len(comparable))
        record, diagnostic = advance(comparable, result, manifest, budget)
        metadata = {"run", "result_sha256", "result_snapshot", "epoch", "calibration_id", "bench_transaction", "bench_attempt_id"}
        if record != {k: v for k, v in stored.items() if k not in metadata}:
            raise ValueError("campaign history projection differs from evidence")
        history.append(stored)
    if state["state"] != diagnostic["state"] and not (state["state"] == "incomplete" and state.get("error")):
        raise ValueError("campaign terminal state contradicts convergence history")
    # Done for a generation scope includes its fab artifacts: recomputed here, never trusted from state.
    expected = fab_artifacts(history[-1]["run"]) if history and state["state"] == "criteria_satisfied" else None
    if state.get("fab_artifacts") != expected:
        raise ValueError("campaign fab artifact hashes differ from the final run's sealed fab package")
    # Dispatch is re-derived from the history just verified against evidence, never from state alone.
    plans = verify_campaign_plans(root, state, config)
    from .stage_receipts import verify_campaign_receipts
    verify_campaign_receipts(root, state, steps=config.get("implementation_steps", []), plans=plans)
    from .routing_adapter import verify_campaign_receipt, validate_binding
    routing_binding = state.get('routing_binding')
    if 'routing_adapter' in config and state['history']:
        first = Path(state['history'][0]['run'])
        validate_binding(routing_binding, config['routing_adapter'], read_json(first / 'candidate.json'),
                         state['mutable_inputs'], read_json(first / 'result.json')['policy_files'])
    elif routing_binding is not None:
        raise ValueError('routing binding without a routing adapter and captured source')
    for name in state.get('stage_receipts', {}):
        work = root / 'implementations' / name
        index = read_json(work / 'stage-receipts.json')
        for k, link in enumerate(index['receipts'], 1):
            receipt_path = work / link['path']
            if routing_binding is not None and plans[name][k - 1] == routing_binding['spec']:
                source = Path(state['history'][int(name) - 1]['run'])
                receipt = read_json(receipt_path)
                if receipt['inputs'] != read_json(source / 'candidate.json')['files']:
                    raise ValueError('routing writer changed the verified placed source')
                verify_campaign_receipt(receipt_path.parent, expected={'implementation': int(name),
                    'source_candidate_id': read_json(source / 'candidate.json')['candidate_id'], 'receipt_index': k},
                    binding=routing_binding)
                if receipt['status'] not in ('completed', 'partial'):
                    if ((work / 'proposal.scope.json').exists() or int(name) != len(state['history']) or
                            state['state'] != 'incomplete'):
                        raise ValueError('failed routing attempt cannot have a proposal or later campaign run')
                elif int(name) < len(state['history']):
                    following = read_json(Path(state['history'][int(name)]['run']) / 'candidate.json')
                    if following.get('scope_path') != str(work / 'proposal.scope.json'):
                        raise ValueError('next campaign run was not captured from this routing proposal')
    return {**state, "state": "empirical_superseded"} if feedback_seen else state


def verify_campaign_plans(root, state, config):
    """Re-derive each implementation's step plan and verify its placement records.

    Returns the expected receipt step list of every implementation for receipt verification."""
    from .placement_adapter import verify_attempt, verify_record
    history = state["history"]
    version = state.get("step_plan")
    if "placement_adapter" in config:
        if version != STEP_PLAN:
            raise ValueError("placement adapter requires a campaign step plan")
        if history:
            first = Path(history[0]["run"])
            expected = placement_binding(config, read_json(first / "candidate.json"), first / "inputs",
                                         state["mutable_inputs"])[0]
            if state.get("placement_binding") != expected:
                raise ValueError("campaign placement binding differs from the first captured candidate")
    elif "placement_binding" in state:
        raise ValueError("campaign placement binding without a placement_adapter declaration")
    anchors = state.get("placement_records", {})
    if not isinstance(anchors, dict):
        raise ValueError("malformed placement record anchors")
    implementations = {p.name: p for p in (root / "implementations").glob("*")}
    plans, placed = {}, set()
    for name, work in sorted(implementations.items()):
        if not name.isdigit() or str(int(name)) != name or not 1 <= int(name) <= len(history):
            raise ValueError("implementation names no campaign history entry")
        number = int(name)
        source = Path(history[number - 1]["run"])
        manifest = read_json(source / "candidate.json")
        plan = step_plan(config, manifest, history[number - 1], version)
        plans[name] = plan_receipt_specs(plan, work, config, manifest, state["mutable_inputs"])
        record = work / "placement-adapter.json"
        if not any(entry["kind"] == "placement" for entry in plan):
            if record.exists() or record.is_symlink() or name in anchors:
                raise ValueError("placement record on an implementation without a placement step")
            continue
        placed.add(name)
        bound = placement_binding(config, manifest, source / "inputs", state["mutable_inputs"])[0]
        if bound != state["placement_binding"]:
            raise ValueError("placement policy, envelope or declaration drifted within the campaign")
        complete = (work / "proposal.scope.json").exists()
        if name not in anchors:
            # The implementation stopped before its step plan ran, for example in the writer session.
            if complete or record.exists() or record.is_symlink() or number != len(history) or \
                    state["state"] != "incomplete":
                raise ValueError("placement implementation lacks its anchored adapter record")
            continue
        anchor = anchors[name]
        if anchor is None:
            if complete or record.exists() or record.is_symlink() or number != len(history):
                raise ValueError("placement adapter record differs from its campaign anchor")
            continue
        if record.is_symlink() or not record.is_file() or digest(record) != anchor:
            raise ValueError("placement adapter record differs from its campaign anchor")
        index = next(e["receipt_index"] for e in plan if e["kind"] == "placement") or 1
        args = (config["placement_adapter"], state["mutable_inputs"])
        pinned = {"receipt_index": index, "source_files": state["launch_environment"]["policy_files"]}
        design = work / "review-work/implementation/design"
        if read_json(record).get("status") == "proposed":
            captured = design
            if number < len(history):
                proposal = Path(history[number]["run"])
                if read_json(proposal / "candidate.json").get("scope_path") != str(work / "proposal.scope.json"):
                    raise ValueError("next campaign run was not captured from this placement proposal")
                captured = proposal / "inputs"
            verify_record(work, source / "inputs", captured, manifest, *args, **pinned)
        else:
            if complete or number != len(history) or state["state"] != "incomplete":
                raise ValueError("incomplete placement attempt is not the campaign's final incomplete step")
            verify_attempt(work, source / "inputs", design, manifest, *args, **pinned)
    if set(anchors) - placed:
        raise ValueError("placement record anchors name no placement implementation")
    return plans


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
        from .applicability import pending_recalibration
        pending = bool(state['history']) and pending_recalibration(Path(state['history'][-1]['run']))
        if not state["history"] or (checked["state"] not in ("criteria_satisfied", "empirical_superseded")
                                    and not (checked['state'] == 'incomplete' and pending)):
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
