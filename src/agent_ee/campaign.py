"""One implementation writer, immutable candidates, independent fresh reviews."""

import copy
import json
import shutil
import time
from pathlib import Path

from .convergence import advance
from .locking import writer_lock
from .profiles import validate_profile
from .storage import digest, read_json, write_json

IMPLEMENTATION_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["summary"], "properties": {"summary": {"type": "string"}}}
STEP_PLAN = "agent-ee.step-plan/1"


def writer_editable(config, mutable, files=None):
    """Optional writer subset; coordinator output authority remains mutable_inputs."""
    from .candidate import safe_relative
    if 'writer_editable' not in config:
        return None
    value = config['writer_editable']
    if (not isinstance(value, list) or any(not isinstance(p, str) for p in value) or
            len(set(value)) != len(value)):
        raise ValueError('writer_editable must be a unique list of captured mutable paths')
    try:
        canonical = all(str(safe_relative(p)) == p for p in value)
    except ValueError as exc:
        raise ValueError('writer_editable contains an unsafe candidate path') from exc
    if not canonical or not set(value) <= set(mutable):
        raise ValueError('writer_editable must contain canonical paths within mutable_inputs')
    if files is not None and not set(value) <= set(files):
        raise ValueError('writer_editable names uncaptured paths')
    return value


def check_writer_files(actual, manifest, editable):
    if editable is None:
        return
    if actual.keys() != manifest['files'].keys():
        raise ValueError('writer_editable inventory differs from captured source')
    changed = sorted(p for p, sha in manifest['files'].items() if actual[p] != sha and p not in editable)
    if changed:
        raise ValueError('writer_editable forbids edits outside the writer allowlist: ' + ', '.join(changed))


def check_writer_proposal(design, manifest, state, editable):
    check_proposal(design, manifest, state)
    if editable is not None:
        from .stage_receipts import file_tree
        actual, other = file_tree(design)
        if other:
            raise ValueError('writer_editable tree contains non-files')
        check_writer_files(actual, manifest, editable)


def verify_writer_records(root, state, config):
    """Verify writer boundaries from ordinary receipt indexes, including no-step turns."""
    if 'writer_editable' not in config:
        return
    anchors = state.get('stage_receipts')
    if not isinstance(anchors, dict):
        raise ValueError('writer_editable requires anchored stage receipt indexes')
    for work in (root / 'implementations').glob('*'):
        number = int(work.name)
        source = read_json(Path(state['history'][number - 1]['run']) / 'candidate.json')
        editable = writer_editable(config, state['mutable_inputs'], source['files'])
        if work.name not in anchors:
            if (number != len(state['history']) or state['state'] != 'incomplete' or
                    (work / 'proposal.scope.json').exists()):
                raise ValueError('writer_editable implementation lacks its anchored receipt index')
            continue
        index = read_json(work / 'stage-receipts.json')
        actual = (read_json(work / index['receipts'][0]['path'])['inputs']
                  if index['receipts'] else index['delivered'])
        check_writer_files(actual, source, editable)
        if number < len(state['history']) and not state['history'][number].get('bench_transaction'):
            following = read_json(Path(state['history'][number]['run']) / 'candidate.json')
            if following.get('scope_path') != str(work / 'proposal.scope.json'):
                raise ValueError('writer_editable successor was not captured from the verified proposal')


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


def plan_receipt_specs(plan, work, config, manifest, mutable, state):
    """The exact receipt step declarations a plan produces, in order."""
    specs = []
    for entry in plan:
        if entry["kind"] == "step":
            specs.append(entry["spec"])
        elif entry['kind'] == 'routing':
            from .layer_policy_lineage import planned_spec
            specs.append(planned_spec(config, state, work, manifest, mutable))
        elif entry["receipt_index"] is not None:
            from .placement_adapter import bind_declaration, step_declaration
            bound = bind_declaration(config["placement_adapter"], manifest, mutable)
            specs.append(step_declaration(Path(work) / "placement" / str(entry["receipt_index"]), bound))
    return specs


def placement_binding(config, manifest, inputs, mutable):
    from .placement_adapter import binding
    return binding(config["placement_adapter"], manifest, inputs, mutable)


def implement(root, previous, definition, state, deadline):
    from .intent_retry import implement as repair_intent
    return repair_intent(root, previous, definition, state, deadline, _implement_once)


def _implement_once(root, previous, definition, state, deadline, *, retry=None):
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
    from .design_rules import writer_context as rule_cards
    cards = rule_cards(manifest, previous / "inputs")
    if cards:
        prompt += ("\nDesign-rule cards from the captured rule packs follow. They are guidance, not evidence: "
                   "only fresh checks grade a proposal. A card with graded_by null is advisory and never graded. "
                   "Do not edit rule packs or their role bindings.\n" + json.dumps({"design_rule_cards": cards}))
    config = read_json(root / "config.json")
    if digest(root / "config.json") != state["config_sha256"]:
        raise ValueError("campaign configuration changed")
    plan = step_plan(config, manifest, state["history"][-1], state.get("step_plan"))
    editable = writer_editable(config, state['mutable_inputs'], manifest['files'])
    if editable is not None:
        prompt += ("\nOnly the following writer_editable files may change during your turn. "
                   "Every other captured file remains protected in every stage, including verification.\n" +
                   json.dumps({'writer_editable': editable}))
    if any(entry['kind'] == 'routing' for entry in plan):
        prompt += ("\nThis is a coordinator routing turn. Leave /work/design byte-for-byte unchanged. "
                   "The coordinator alone runs declared plane preparation then the pinned routing adapter; "
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
                   "proposal graded by fresh checks.\n" +
                   ("The declaration also admits the group-local copper of the captured sub-layout request "
                    f"{bound['sublayout']['request']['path']}, for example applied with agent_ee.sublayout: only "
                    "each whole group whose members are all new, copied exactly from its captured sub-board "
                    "onto the voted nets, inside the group region, with each copied zone's saved fill exactly "
                    "its refill (refill after any later change near it). Any other copper refuses the iteration.\n"
                    if bound.get("sublayout") else "") + json.dumps({
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
                           **({'inputs': spec['inputs']} if 'inputs' in spec else {}),
                           'scheduled': spec['stage'] in scheduled_stages} for spec in config['implementation_steps']],
                       'step_owned_outputs': sorted({rel for spec in scheduled for rel in spec['writes']})}))
    if retry is not None:
        from .intent_retry import seed
        prompt += seed(work, *retry)
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
            invariant=lambda: check_writer_proposal(design, manifest, state, editable))
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
    check_writer_proposal(design, manifest, state, editable)
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
        from .intent_retry import pin_source
        pin_source(work, manifest, state, config, plan)
        run_implementation_steps(work, design, manifest, state, deadline, plan,
                                 source_inputs=previous / "inputs", declaration=config.get("placement_adapter"),
                                 config=config)
        if plan:
            check_proposal(design, manifest, state)
    elif plan:
        raise ValueError("campaign predates stage receipts; start a fresh campaign to run implementation steps")
    from .rating_lineage import proposal_scope
    # A newly adopted binding-fill --ratings receipt is captured into a new derived scope.
    scope = proposal_scope(manifest["scope"], design, work, root, state, config)
    from .layer_policy_lineage import proposal_scope as layer_policy_scope
    # A routing turn's pre-routing layer policy derivation is captured the same way.
    scope = layer_policy_scope(scope, work, state, config)
    from .placement_defects import proposal_scope as placement_evidence_scope
    scope = placement_evidence_scope(scope, work, root, state)
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
            message = f"implementation changed immutable verification input: {rel}"
            board = Path(rel).with_suffix(".kicad_pcb").as_posix()
            if (Path(rel).suffix in (".kicad_pro", ".kicad_prl") and board in actual
                    and actual[board] != manifest["files"][board]):
                message += (f"; likely pcbnew.SaveBoard side effect: saving sibling board {board} "
                            "can rewrite .kicad_pro/.kicad_prl. Project siblings remain immutable. "
                            "Save boards through a sandboxed step runner that restores or excludes "
                            "project siblings, or use a staging copy and copy back only the board.")
            raise ValueError(message)
    for rel, sha in state["baseline"]["files"].items():
        if not (design / rel).is_file() or digest(design / rel) != sha:
            raise ValueError(f"implementation changed immutable baseline source: {rel}")
    # Tool programs are part of the frozen verification harness, never repair targets.
    for spec in manifest["scope"]["checks"]:
        adapter = spec.get("params", {}).get("adapter")
        if adapter and (not (design / adapter).is_file() or digest(design / adapter) != manifest["files"][adapter]):
            raise ValueError("implementation changed a verification adapter")


def validate_routing_predecessors(steps, scope):
    """Only the declared plane producer may precede the owned routing adapter."""
    from .stage_receipts import validate_step
    preceding = [validate_step(s, host=False) for s in steps if s['stage'] == 'routing']
    if not preceding:
        return
    if (len(preceding) != 1 or preceding[0]['step'] != 'add-planes' or
            set(preceding[0]['writes']) != {scope['board']} or preceding[0]['adoptable_exits'] != [0]):
        raise ValueError('routing_adapter cannot be combined with routing implementation_steps '
                         'except one add-planes producer writing only the board and adopting exit 0')


def verify_routing_chain(work, index, expected, specs, *, terminal_incomplete):
    """Replay exact producer inputs from the captured placed source, including failed prefixes."""
    from .stage_receipts import ADOPTABLE, after_tree
    previous_failed = False
    for position, link in enumerate(index['receipts']):
        receipt = read_json(work / link['path'])
        if previous_failed:
            raise ValueError('routing producer ran after a failed predecessor')
        if receipt['inputs'] != expected:
            raise ValueError('routing receipt chain differs from the captured source or preceding output')
        if receipt['spec'] != specs[position]:
            raise ValueError('routing receipt chain differs from the declared producer sequence')
        previous_failed = receipt['status'] not in ADOPTABLE
        if not previous_failed:
            expected = after_tree(receipt)
    complete = len(index['receipts']) == len(specs) and not previous_failed
    if not complete and (not terminal_incomplete or (work / 'proposal.scope.json').exists()):
        raise ValueError('unfinished routing producer chain must be the final incomplete implementation')
    if (work / 'proposal.scope.json').exists() and index['delivered'] != expected:
        raise ValueError('routing proposal differs from the adopted receipt chain')


def run_implementation_steps(work, design, manifest, state, deadline, plan, *, source_inputs=None,
                             declaration=None, config=None):
    """Coordinator-run planned steps on the delivered design; receipts are provenance only."""
    from .stage_receipts import adopt, bind_implementation, run_step, validate_step

    number = int(work.name)
    routing_turn = any(entry['kind'] == 'routing' for entry in plan)
    # Only routing turns verify the receipt chain against the captured tree (AGENTEE-197 seam);
    # other turns keep their historical manifest contract.
    expected_files = dict(manifest['files']) if routing_turn else None
    try:
        if routing_turn:
            from .routing_adapter import validate_live_binding
            from .stage_receipts import file_tree
            if file_tree(design) != (expected_files, []):
                raise ValueError('routing writer changed the verified placed source')
            validate_live_binding(state['routing_binding'])
        for entry in plan:
            if routing_turn and file_tree(design) != (expected_files, []):
                raise ValueError('routing producer input differs from preceding adopted receipt')
            if entry['kind'] == 'routing':
                from .routing_adapter import validate_live_binding, verify_campaign_receipt
                from .stage_receipts import file_tree
                binding = state['routing_binding']
                validate_live_binding(binding)
                from . import layer_policy_lineage as lineage
                if config is not None and lineage.config_of(config) is not None:
                    # Pre-routing boundary: the coordinator measures the adopted planed board and
                    # derives the bound; the anchored record fixes it before the router starts.
                    board = manifest['scope']['board']
                    record = lineage.boundary(work, design, board, expected_files[board], config, number,
                                              manifest['candidate_id'])
                    state.setdefault('layer_policy_records', {})[work.name] = digest(
                        work / lineage.BOUNDARY / lineage.RECORD_FILE)
                    if record['status'] != 'derived':
                        raise ValueError(f"layer policy unknown at the pre-routing boundary: {record['reason']}; "
                                         "routing not run")
                    binding = lineage.turn_binding(binding, lineage.routing_spec(
                        config, manifest, state['mutable_inputs'], record['layer_policy']))
                spec = binding['spec']
                k = entry['receipt_index']
                receipt_dir = work / 'receipts' / f"{k}-{spec['step']}"
                expected = {'implementation': number, 'source_candidate_id': manifest['candidate_id'],
                            'receipt_index': k}
                receipt = run_step(receipt_dir, design, spec, expected, deadline=deadline)
                if receipt['inputs'] != expected_files:
                    raise ValueError('routing receipt chain input differs from preceding adopted output')
                verify_campaign_receipt(receipt_dir, expected=expected, binding=binding)
                adopt(receipt_dir, receipt, design)
                from .stage_receipts import after_tree
                expected_files = after_tree(receipt)
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
            if routing_turn and receipt['inputs'] != expected_files:
                raise ValueError('routing predecessor receipt input differs from captured source')
            # A failed, partial-but-unadoptable or refused step stops the repair; no default output.
            adopt(receipt_dir, receipt, design)
            if routing_turn:
                from .stage_receipts import after_tree
                expected_files = after_tree(receipt)
        if routing_turn and file_tree(design) != (expected_files, []):
            raise ValueError('routing delivered tree differs from adopted receipt chain')
        if routing_turn:
            from .final_planes import consume_routing
            consume_routing(work, design, manifest, plan, deadline)
    finally:
        state["stage_receipts"][work.name] = bind_implementation(work, design, number, manifest["candidate_id"])


MAX_ITERATIONS = 100


def validate_iterations(value):
    """The engine's iteration range; drivers that write campaigns validate against it, not a policy of their own."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ITERATIONS:
        raise ValueError(f"max_iterations must be between 1 and {MAX_ITERATIONS}")
    return value


def run_campaign(config_path, out):
    from .cli import run_review, verify, policy_files, runtime_files
    from .measurements import finite

    config_path = Path(config_path).resolve()
    config = read_json(config_path)
    if config.get("schema") != "agent-ee.campaign/1":
        raise ValueError("expected agent-ee.campaign/1")
    from .review_run import resubmission_limit
    config["max_schema_resubmissions"] = resubmission_limit(config.get("max_schema_resubmissions", 1))
    iterations = validate_iterations(config["max_iterations"])
    seconds = finite(config["max_seconds"], "max_seconds")
    if not 1 <= seconds <= 86400:
        raise ValueError("max_seconds must be between 1 and 86400")
    scope_path = (config_path.parent / config["scope"]).resolve()
    scope = read_json(scope_path)
    if scope.get("schema") not in ("agent-ee.scope/2", "agent-ee.scope/3"):
        raise ValueError("campaign requires an immutable scope/2 or scope/3 baseline")
    if 'writer_editable' in config:
        from .candidate import source_manifest
        mutable = scope['mutable_inputs'] if 'mutable_inputs' in scope else [scope['board'], scope['schematic']]
        writer_editable(config, mutable)
        files, _, _ = source_manifest(scope, scope_path.parent)
        writer_editable(config, mutable, files)
    from .stages import finding_retry_limit
    stages = scope.get('stages')
    if isinstance(stages, dict) and 'finding_retry_limit' in stages:
        finding_retry_limit(stages['finding_retry_limit'])
    profile = validate_profile(config["review_profile"])
    steps = config.get("implementation_steps", [])
    if not isinstance(steps, list):
        raise ValueError("implementation_steps must be a list of agent-ee.stage-step/1 declarations")
    from .stage_receipts import validate_step
    for spec in steps:
        validate_step(spec)
    from .stage_receipts import validate_pre_receipts, validate_pre_receipt_chain, import_pre_receipts
    from .placement_defects import validate_campaign as validate_placement_provenance
    validate_placement_provenance(scope)
    pre_receipts = validate_pre_receipts(config.get('pre_receipts', []))
    validate_pre_receipt_chain(config_path.parent, pre_receipts)
    if 'routing_adapter' in config:
        from .routing_adapter import campaign_spec, validate_campaign_step
        spec = campaign_spec(config['routing_adapter'], {'scope': scope}, scope.get('mutable_inputs',
                             [scope['board'], scope['schematic']]))
        validate_campaign_step(spec)
        validate_routing_predecessors(steps, scope)
    if "placement_adapter" in config:
        from .placement_adapter import validate_declaration
        validate_declaration(config["placement_adapter"])
        stages = scope.get("stages")
        if not isinstance(stages, dict) or "placement" not in stages.get("order", []):
            raise ValueError("placement_adapter requires a staged scope with a placement stage")
        if any(spec["stage"] == "placement" for spec in steps):
            # One placement writer path per iteration: never run the same helper twice.
            raise ValueError("placement_adapter cannot be combined with placement implementation_steps")
    from .rating_lineage import validate_campaign as validate_rating_provenance
    validate_rating_provenance(config, scope, [validate_step(s, host=False) for s in steps])
    from .layer_policy_lineage import validate_campaign as validate_layer_policy_derivation
    validate_layer_policy_derivation(config, scope)
    from .layer_policy_lineage import pilot_link, verify_pilot
    pilot_tool = None
    if pilot_link(config) is not None:
        from .stage_receipts import observe_tool
        # AGENTEE-265: V_i is re-derived from the retained pilot attempt facts, never taken as stated.
        pilot_tool = observe_tool(spec)
        verify_pilot(config_path.parent, config, imported=False, tool=pilot_tool)
    # Reuse the subscription route validator for the implementation definition.
    validate_profile({**profile, "adjudicator": config["implementation"]})
    root = Path(out).resolve()
    with writer_lock(root):
        root.mkdir(parents=True, exist_ok=False)
        (root / ".campaign-root").touch()
        if 'pre_receipts' in config:
            config['pre_receipts'] = import_pre_receipts(root, pre_receipts, config_path.parent)
        if pilot_tool is not None:
            from .layer_policy_lineage import import_pilot
            config = import_pilot(root, config, config_path.parent, tool=pilot_tool)
        write_json(root / "config.json", config)
        write_json(root / "profile.json", profile)
        state = {"schema": "agent-ee.campaign-state/1", "state": "running", "history": [],
                 "config_sha256": digest(root / "config.json"), "stage_receipts": {}, "step_plan": STEP_PLAN,
                 "max_schema_resubmissions": config['max_schema_resubmissions'],
                 "implementation_records": {},
                 "launch_environment": {"policy_files": policy_files(), "runtime_files": runtime_files()},
                 "implementation_definition": {**config["implementation"], "tool_mode": "investigate"}}
        if 'pre_receipts' in config:
            state['pre_receipts'] = {link['path']: link['sha256'] for link in config['pre_receipts']}
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
    from .rating_lineage import sealed_baseline, verify_candidate
    baseline = sealed_baseline(run, config)
    verify_candidate(root, state, config, len(state["history"]) + 1, manifest)
    from .layer_policy_lineage import verify_candidate as verify_layer_policy_provenance
    verify_layer_policy_provenance(root, state, config, len(state["history"]) + 1, manifest)
    from .placement_defects import verify_candidate as verify_placement_provenance
    verify_placement_provenance(root, state, len(state["history"]) + 1, manifest, run / 'inputs')
    if "baseline" not in state:
        state["baseline"] = baseline
        state["mutable_inputs"] = mutable_inputs(manifest, baseline)
        writer_editable(config, state['mutable_inputs'], manifest['files'])
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
    from .fab_stage import campaign_context, pending_checks, verify_check_context
    context = campaign_context(root, state, config, manifest['scope'])
    verify_check_context(run, result, context)
    from .assembly_readiness import campaign_stock_context, verify_stock_context
    verify_stock_context(run, result, campaign_stock_context(root, state, config, manifest['scope']),
                         baseline=baseline)
    record, diagnostic = advance(comparable, result, manifest, budget, pending_fab=pending_checks(result))
    from .placement_defects import acceptance as placement_acceptance
    diagnostic = placement_acceptance(run, diagnostic)
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
        from .board_envelope import require_final
        try:
            # The same captured-baseline guard as the review outcome and fab release (AGENTEE-197).
            require_final(run, result["candidate_id"])
        except ValueError as exc:
            state.update(state="incomplete", error=f"ValueError: {exc}")
    if state["state"] == "criteria_satisfied":
        artifacts = fab_artifacts(run)
        if artifacts is not None:
            state["fab_artifacts"] = artifacts
    state.pop("stage_done", None)
    if state["state"] == "criteria_satisfied":
        # Decision 1 amendment: each declared stage's graded content must be its step receipt's output.
        from .stage_done import record_done
        record_done(root, state, config)
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
                from .fab_stage import campaign_context
                context = campaign_context(root, state, config, read_json(scope_path))
                from .assembly_readiness import campaign_stock_context
                stock_context = campaign_stock_context(root, state, config, read_json(scope_path))
                from .rf_review import campaign_routing_context
                from .rating_lineage import sealed_baseline
                run_review(scope_path, run, deadline - time.monotonic(), profile=root / "profile.json",
                           expected_baseline=state.get("baseline"),
                           baseline_view=lambda captured: sealed_baseline(captured, config),
                           max_schema_resubmissions=config.get("max_schema_resubmissions", 1), fab_context=context,
                           stock_context=stock_context, routing_context=campaign_routing_context(root, state))
            initial_run = None
            record_iteration(root, state, run, config)
            verify_campaign(root)
            if state["state"] != "repair":
                return 0 if state["state"] == "criteria_satisfied" else 1
            with writer_lock(run):
                verify(run, invoked=[config["implementation"]])
                scope_path = implement(root, run, config["implementation"], state, deadline)
    except (ValueError, OSError, KeyError, TypeError, KeyboardInterrupt) as exc:
        state.update(state="incomplete", error=f"{type(exc).__name__}: {exc}")
        write_json(root / "state.json", state)
        return 1


def verify_campaign(root, *, observations=None):
    root = Path(root).resolve()
    with writer_lock(root):
        return verify_campaign_records(root, observations=observations)


def verify_campaign_records(root, *, observations=None):
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
    # The writer ran in this campaign, so its harness gates drift for every run.
    writer = [config['implementation']] if config.get('implementation') else []
    history = []
    diagnostic = {"state": "running"}
    feedback_seen = False
    # One observation of the host's policy and runtime files serves every retained run (AGENTEE-250).
    host = {}
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
        changes = []
        current = verify(run, current_source=False, observations=changes, invoked=writer, host=host)
        if observations is not None:
            # Every retained run reports the same unused-harness change; record it once.
            observations.extend(change for change in changes if change not in observations)
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
        from .rating_lineage import sealed_baseline, verify_candidate
        if sealed_baseline(run, config) != state["baseline"]:
            raise ValueError("campaign history baseline changed")
        verify_candidate(root, {**state, 'history': history}, config, index, manifest)
        from .layer_policy_lineage import verify_candidate as verify_layer_policy_provenance
        verify_layer_policy_provenance(root, {**state, 'history': history}, config, index, manifest)
        empirical = manifest["scope"].get("empirical")
        calibration_id = manifest["files"].get(empirical) if empirical else None
        if stored["calibration_id"] != calibration_id:
            raise ValueError("campaign epoch calibration identity changed")
        if not history:
            if state["mutable_inputs"] != mutable_inputs(manifest, state["baseline"]):
                raise ValueError("campaign mutable_inputs differ from captured write authority")
            writer_editable(config, state['mutable_inputs'], manifest['files'])
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
        from .fab_stage import campaign_context, pending_checks, verify_check_context
        preceding = {**state, 'history': history}
        context = campaign_context(root, preceding, config, manifest['scope'])
        verify_check_context(run, result, context)
        from .assembly_readiness import campaign_stock_context, verify_stock_context
        verify_stock_context(run, result, campaign_stock_context(root, preceding, config, manifest['scope']),
                             baseline=state['baseline'])
        record, diagnostic = advance(comparable, result, manifest, budget, pending_fab=pending_checks(result))
        from .placement_defects import acceptance as placement_acceptance
        diagnostic = placement_acceptance(run, diagnostic)
        metadata = {"run", "result_sha256", "result_snapshot", "epoch", "calibration_id", "bench_transaction", "bench_attempt_id"}
        if record != {k: v for k, v in stored.items() if k not in metadata}:
            raise ValueError("campaign history projection differs from evidence")
        history.append(stored)
    if state["state"] == "criteria_satisfied" and history:
        # Replay re-applies the final guard to the captured baseline, never to the stored acceptance.
        from .board_envelope import require_final
        # result is the final run's history snapshot, verified against its evidence above.
        require_final(history[-1]["run"], result["candidate_id"])
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
    from .placement_defects import verify_candidate as verify_placement_provenance
    for number, stored in enumerate(state['history'], 1):
        manifest = read_json(Path(stored['run']) / 'candidate.json')
        verify_placement_provenance(root, state, number, manifest, Path(stored['run']) / 'inputs')
    verify_writer_records(root, state, config)
    from .intent_retry import verify as verify_intent_retries
    verify_intent_retries(root, state, config)
    from .routing_adapter import verify_campaign_receipt, validate_binding
    routing_binding = state.get('routing_binding')
    if 'routing_adapter' in config and state['history']:
        first = Path(state['history'][0]['run'])
        validate_binding(routing_binding, config['routing_adapter'], read_json(first / 'candidate.json'),
                         state['mutable_inputs'], read_json(first / 'result.json')['policy_files'])
    elif routing_binding is not None:
        raise ValueError('routing binding without a routing adapter and captured source')
    from . import layer_policy_lineage as lineage
    from .stage_receipts import after_tree
    derivations = {}
    for name in state.get('stage_receipts', {}):
        work = root / 'implementations' / name
        index = read_json(work / 'stage-receipts.json')
        # A derived layer policy changes only the routing step's --layer-policy argument.
        turn = None
        if routing_binding is not None and lineage.config_of(config) is None:
            turn = routing_binding if routing_binding['spec'] in plans[name] else None
        elif routing_binding is not None:
            routes = [s for s in plans[name] if lineage.declared_form(s) == routing_binding['spec']]
            turn = lineage.turn_binding(routing_binding, routes[0]) if routes else None
        if turn is not None:
            source = Path(state['history'][int(name) - 1]['run'])
            verify_routing_chain(work, index, read_json(source / 'candidate.json')['files'], plans[name],
                terminal_incomplete=int(name) == len(state['history']) and state['state'] == 'incomplete')
            if lineage.config_of(config) is not None:
                # The router's input is the adopted output of the producers declared before it.
                routing_input, routed = dict(read_json(source / 'candidate.json')['files']), None
                for k, link in enumerate(index['receipts'], 1):
                    receipt = read_json(work / link['path'])
                    if plans[name][k - 1] == turn['spec']:
                        routed = receipt
                        break
                    if receipt['status'] not in ('completed', 'partial'):
                        break
                    routing_input = after_tree(receipt)
                derivations[name] = lineage.verify_turn(root, state, config, int(name),
                                                        routing_input[source_board(source)], routed)
        for k, link in enumerate(index['receipts'], 1):
            receipt_path = work / link['path']
            if turn is not None and plans[name][k - 1] == turn['spec']:
                source = Path(state['history'][int(name) - 1]['run'])
                receipt = read_json(receipt_path)
                verify_campaign_receipt(receipt_path.parent, expected={'implementation': int(name),
                    'source_candidate_id': read_json(source / 'candidate.json')['candidate_id'], 'receipt_index': k},
                    binding=turn)
                if receipt['status'] not in ('completed', 'partial'):
                    if ((work / 'proposal.scope.json').exists() or int(name) != len(state['history']) or
                            state['state'] != 'incomplete'):
                        raise ValueError('failed routing attempt cannot have a proposal or later campaign run')
                elif int(name) < len(state['history']):
                    following = read_json(Path(state['history'][int(name)]['run']) / 'candidate.json')
                    if following.get('scope_path') != str(work / 'proposal.scope.json'):
                        raise ValueError('next campaign run was not captured from this routing proposal')
    lineage.verify_records(root, state, config, derivations)
    lineage.verify_pilot(root, config, imported=True, tool=(routing_binding or {}).get('tool'))
    from .stage_done import verify_done
    verify_done(root, state, config)
    return {**state, "state": "empirical_superseded"} if feedback_seen else state


def source_board(source):
    return read_json(Path(source) / 'candidate.json')['scope']['board']


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
        if 'routing_adapter' in config:
            validate_routing_predecessors(config.get('implementation_steps', []), manifest['scope'])
        plan = step_plan(config, manifest, history[number - 1], version)
        plans[name] = plan_receipt_specs(plan, work, config, manifest, state["mutable_inputs"], state)
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
                proposal_manifest = read_json(proposal / 'candidate.json')
                from .placement_defects import DIRECTORY, delivered_view
                if any(p.startswith(DIRECTORY + '/') for p in proposal_manifest['files']):
                    from .placement_adapter import _files
                    delivered = _files(design)
                    if delivered_view(proposal_manifest['files'], delivered) != delivered:
                        raise ValueError('next campaign capture differs from the placement proposal')
                    captured = design
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
