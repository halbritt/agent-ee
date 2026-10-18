"""Rung 1 stage-ordered driver: one RC candidate through the fixed stage order, no inference.

Run ``python3 scripts/rung1_stages.py run --out RUNDIR (--reference SCOPE | --spec SPEC)``
and ``python3 scripts/rung1_stages.py verify RUNDIR`` (see docs/rung1-stages.md, AGENTEE-104).

The stage order is fixed: 1 requirements, 2 parts, 3 schematic, 4 placement, 5 routing,
6 verification, 7 fab. A run starts at an entry stage. Stages before it are ``not_reached``:
their content was supplied to the run, not produced by their step. From the entry on, each
stage runs its declared ``agent-ee.stage-step/1`` steps through ``stage_receipts.run_step``
and adopts them, then grades the stage's checks from a sealed checks-only review. The first
stage that is absent (no declared step), fails or lacks coverage stops the driver, and every
later stage is ``not_reached``, never passed.

A stage is ``pass`` only from sealed check results whose graded design is exactly the tree
the stage's last receipt produced (``stage_receipts.applicability``). A stage with steps and
no checks is ``completed``. The fab stage is the ``fab_package`` done gate (AGENTEE-94).
The driver composes ``rung1_qualified``; it adds no step, check or campaign code. Its record
is ``experimental-unqualified`` and is the precursor run that AGENTEE-96 replaces.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import rung1_campaign as rc
from . import rung1_qualified as rq
from .storage import canonical, digest, identity, read_json, write_json

QUALIFICATION = 'experimental-unqualified'
RECORD_SCHEMA = 'agent-ee.rung1-stage-record/0-experimental'
RECORD = 'rung1-stage-record.json'
PACKAGE_ROOT = rq.PACKAGE_ROOT
REPO_ROOT = rq.REPO_ROOT
REQUIREMENT_RECORD = REPO_ROOT / 'fixtures' / 'rung1' / 'requirements.record.json'
MOUNT_SOURCE = '/tool/agent-ee'
# (number, name, stage_receipts stage, item that owns the stage's producer)
STAGES = (
    (1, 'requirements', 'scaffold', 'AGENTEE-100'),
    (2, 'parts', 'parts', 'AGENTEE-101'),
    (3, 'schematic', 'schematic', 'AGENTEE-102'),
    (4, 'placement', 'placement', 'AGENTEE-91'),
    (5, 'routing', 'routing', 'AGENTEE-93'),
    (6, 'verification', 'verification', 'AGENTEE-104'),
    (7, 'fab', 'fab', 'AGENTEE-94'),
)
NAMES = [name for _, name, _, _ in STAGES]
STATUSES = ('pass', 'completed', 'failed', 'missing_coverage', 'absent', 'not_reached')
DONE_RULE = ('every stage from requirements to fab is pass or completed, the fab stage is a passing fab_package '
             'done check, and each graded design is exactly what the preceding stage receipt produced')
PARTS_ABSENT = ('no agent-ee.stage-step/1 declares the parts stage: parts are selected in-process by '
                'agent_ee.generate stage 2 (rung1_parts with --parts-manifest) during preparation; '
                'moving the rung-1 drivers onto a declared producer is AGENTEE-134')


class StageRefusal(ValueError):
    """The run could not be set up; no stage is graded."""


# ------------------------------------------------------------------ declarations

def requirements_step(source_root=PACKAGE_ROOT):
    """Stage 1: the AGENTEE-100 compiler, unchanged, on the captured record and spec (artifact-only)."""
    code = ('import sys; sys.path.insert(0, %r); from agent_ee.requirement_scope import main; '
            'raise SystemExit(main())' % MOUNT_SOURCE)
    return {'schema': 'agent-ee.stage-step/1', 'stage': 'scaffold', 'step': 'compile-requirements',
            'argv': ['/usr/bin/python3', '-B', '-c', code, '/candidate/requirements.record.json',
                     '--out', '/work/compiled'],
            'mounts': [{'source': str(Path(source_root)), 'at': MOUNT_SOURCE}], 'source': MOUNT_SOURCE,
            'version_argv': ['/usr/bin/python3', '--version'], 'writes': {},
            'artifacts': ['compiled/scope.json', 'compiled/receipt.json', 'compiled/scaffold'],
            'adoptable_exits': [0], 'timeout_s': 300}


def done_gate(scope):
    """The scope with its fab stage graded by the package gate and bound DFM consumers.

    A scope with a terminal fab_package producer is returned unchanged. Otherwise fab_export and
    fab_consistency are replaced by one fab_package check (which runs both) and the requirements
    that mapped to them map to it. Generation scopes also carry the generation marker."""
    scope = copy.deepcopy(scope)
    checks = scope['checks']
    packages = [c for c in checks if c['kind'] == 'fab_package']
    if packages:
        from .fab_package import require_last
        if len(packages) != 1:
            raise StageRefusal('the fab stage needs exactly one fab_package check')
        try:
            require_last(checks, packages[0])
        except ValueError as exc:
            raise StageRefusal(str(exc)) from exc
        return scope
    netlist = [c['id'] for c in checks if c['kind'] == 'netlist']
    if len(netlist) != 1:
        raise StageRefusal('the fab_package done gate needs exactly one netlist check')
    replaced = {c['id'] for c in checks if c['kind'] in ('fab_export', 'fab_consistency')}
    done_id = next((c['id'] for c in checks if c['kind'] == 'fab_export'), 'fab')
    if done_id in {c['id'] for c in checks} - replaced:
        raise StageRefusal(f'check id {done_id} is taken')
    done = {'id': done_id, 'kind': 'fab_package', 'params': {'netlist_check': netlist[0]}}
    if 'stages' in scope:
        done['stage'] = 'fab'
    scope['checks'] = [c for c in checks if c['id'] not in replaced] + [done]
    for requirement in (scope.get('baseline') or {}).get('requirements', []):
        mapped = [done_id if c in replaced else c for c in requirement['checks']]
        requirement['checks'] = list(dict.fromkeys(mapped))
    if scope['schema'] in ('agent-ee.scope/2', 'agent-ee.scope/3'):
        scope['generation'] = {'schema': 'agent-ee.generation/1', 'done_check': done_id}
    return scope


def stage_checks(scope):
    """Check IDs per driver stage: the check's declared stage, else verification (fab for package/DFM)."""
    grouped = {name: [] for name in NAMES}
    for check in scope['checks']:
        stage = check.get('stage') or ('fab' if check['kind'] in ('fab_package', 'jlc_dfm') else 'verification')
        if stage not in grouped:
            raise StageRefusal(f"check {check['id']} declares stage {stage}, which is not a rung-1 stage")
        grouped[stage].append(check['id'])
    return grouped


# ------------------------------------------------------------------ run setup

def absolute_inputs(scope, base):
    scope = copy.deepcopy(scope)
    scope['inputs'] = [{**i, 'path': i['path'] if os.path.isabs(i['path']) else str((Path(base) / i['path']).resolve())}
                       for i in scope['inputs']]
    return scope


def reference_setup(rundir, scope_path):
    """A supplied finished board enters at verification; stages 1-5 did not produce it."""
    scope = done_gate(absolute_inputs(read_json(scope_path), Path(scope_path).parent))
    path = rundir / 'scope.json'
    write_json(path, scope)
    design = rundir / 'design'
    rq.materialize_inputs(path, design)
    plan = {name: {'checks': ids} for name, ids in stage_checks(scope).items() if ids}
    supplied = {'reference_scope': {'path': str(Path(scope_path).absolute()), 'sha256': digest(scope_path)}}
    return {'entry': 'verification', 'scope': scope, 'scope_path': path, 'design': design, 'plan': plan,
            'supplied': supplied}


def requirements_setup(rundir, record_path):
    """Stage 1 on the captured requirement record and spec; nothing else is prepared."""
    record_path = Path(record_path)
    record = read_json(record_path)
    design = rundir / 'design'
    design.mkdir()
    shutil.copyfile(record_path, design / 'requirements.record.json')
    spec = record['spec']['path']
    if os.path.isabs(spec) or Path(spec).name != spec:
        raise StageRefusal('the requirement record must name its spec beside it')
    shutil.copyfile(record_path.parent / spec, design / spec)
    plan = {'requirements': {'steps': [requirements_step()]}}
    return {'entry': 'requirements', 'scope': None, 'scope_path': None, 'design': design, 'plan': plan,
            'supplied': {'requirement_record': {'path': str(record_path.absolute()), 'sha256': digest(record_path)}}}


def generation_setup(rundir, spec, *, kicad_route, catalog_checkout=None, parts_manifest=None):
    """rung1_qualified's preparation supplies the empty candidate; the run enters at schematic."""
    from .routing_adapter import preflight
    try:
        prep = rq.prepare(spec, rundir, catalog_checkout, parts_manifest)
        scaffold, _ = rq.write_scaffold(rundir, prep)
        loads = rc.scaffold_load_checks(rundir, scaffold)
        if not all(entry['loaded'] for entry in loads.values()):
            raise StageRefusal('kicad-cli did not load the empty scaffold: ' + json.dumps(loads))
        scope = done_gate(rq.compile_staged_scope(prep['params'], rundir, scaffold))
    except (rq.PrepareFailure, rc.PrepareFailure) as exc:
        raise StageRefusal(f'{type(exc).__name__}: {exc}') from exc
    path = rundir / 'scope.json'
    write_json(path, scope)
    validation = rq.validate_scope_file(path, rundir / 'scope-validation-inputs')
    steps = rq.check_steps(rq.step_specs(kicad_route), scope['mutable_inputs'])
    design = rundir / 'design'
    rq.materialize_inputs(path, design)
    checks = stage_checks(scope)
    plan = {name: {'checks': ids} for name, ids in checks.items() if ids}
    for name in ('schematic', 'placement', 'routing'):
        plan.setdefault(name, {})
    plan['schematic']['steps'] = [s for s in steps if s['stage'] == 'schematic']
    plan['placement']['placement'] = rq.placement_declaration()
    plan['routing']['steps'] = [s for s in steps if s['stage'] == 'routing']
    supplied = {'spec': {'path': str(Path(spec).absolute()), 'sha256': digest(spec)},
                'prep_receipts': prep['prep_receipts'],
                'scaffold': {'path': 'scaffold.json', 'sha256': digest(rundir / 'scaffold.json'), 'load_checks': loads},
                'scope_validation': validation,
                'kicad_route': {**rc.route_identity(kicad_route), 'preflight': preflight(kicad_route)}}
    return {'entry': 'schematic', 'scope': scope, 'scope_path': path, 'design': design, 'plan': plan,
            'supplied': supplied}


# ------------------------------------------------------------------ stage execution

def review(rundir, setup, label):
    """A checks-only review with retained sources that later adoptions cannot mutate."""
    scope_path = rq.rehearsal_scope(setup['scope_path'], setup['design'], rundir / 'reviews' / f'{label}.scope.json')
    source = rundir / 'reviews' / f'{label}-source'
    rq.materialize_inputs(scope_path, source)
    scope_path = rq.rehearsal_scope(setup['scope_path'], source, scope_path)
    out = rundir / 'reviews' / label
    command = rc.run_logged(rc.agent_ee_command('review', scope_path, '--out', out, '--checks-only'),
                            rundir / 'logs', f'review-{label}', 3600)
    return out, {k: command[k] for k in ('argv', 'exit_code', 'stderr_tail')}


def sealed(run):
    """Verify the review run as `agent-ee verify` does; the error text, or None."""
    from .cli import verify
    try:
        verify(run)
    except Exception as exc:  # noqa: BLE001 - an unverifiable run is missing coverage, never a pass
        return f'{type(exc).__name__}: {exc}'
    return None


def grade(run, ids):
    """Stage status from the sealed per-check results: pass, failed or missing_coverage."""
    checks, defects, missing = {}, [], []
    for cid in ids:
        path = Path(run) / 'checks' / f'{cid}.json'
        if not path.is_file():
            checks[cid] = None
            missing.append(f'{cid}: no sealed result')
            continue
        result = read_json(path)
        checks[cid] = {'execution': result.get('execution'), 'outcome': result.get('outcome'),
                       'summary': result.get('summary'), 'path': f'checks/{cid}.json', 'sha256': digest(path)}
        if result.get('execution') != 'ok' or result.get('outcome') == 'unknown':
            missing.append(f"{cid}: {result.get('execution')}/{result.get('outcome')} ({result.get('summary')})")
        elif result.get('outcome') == 'defect':
            defects.append(cid)
        elif result.get('outcome') not in ('pass', 'info'):
            missing.append(f"{cid}: outcome {result.get('outcome')}")
    if not ids:
        return 'missing_coverage', 'no check grades this stage', checks
    if defects:
        return 'failed', 'defect: ' + ', '.join(defects), checks
    if missing:
        return 'missing_coverage', '; '.join(missing), checks
    return 'pass', None, checks


def produced_binding(run, receipt_paths):
    """Whether the graded candidate is exactly what the last adoptable receipt produced."""
    from .stage_receipts import applicability
    if not receipt_paths:
        return {'applicability': 'supplied', 'differences': []}
    receipts = [read_json(p) for p in receipt_paths]
    delivered = read_json(Path(run) / 'candidate.json')['files']
    return applicability(receipts, delivered)[-1]


def run_stage_steps(base, design, entry, scope, last_review):
    """The stage's declared steps through run_step and adopt; (receipt summaries, ok)."""
    if 'placement' in entry:
        try:
            summary, ok = rq.rehearse_placement(base, design, last_review, scope)
        except ValueError as exc:  # the adapter refused to bind; no helper ran
            return [{'step': 'agent-ee-placer', 'status': 'refused', 'refused': str(exc)}], False
        return [summary], ok
    return rq.run_steps(base, design, entry['steps'], 1)


def run_plan(rundir, setup):
    rundir = Path(rundir)
    entry_index = NAMES.index(setup['entry'])
    design, plan = setup['design'], setup['plan']
    stages, stopped, last_review, current, receipts = [], None, None, False, []
    for number, name, receipt_stage, owner in STAGES:
        record = {'number': number, 'stage': name, 'receipt_stage': receipt_stage, 'owner': owner,
                  'status': None, 'reason': None, 'receipts': [], 'review': None, 'checks': {}}
        stages.append(record)
        entry = plan.get(name)
        if NAMES.index(name) < entry_index:
            record.update(status='not_reached', reason=f"supplied: the run entered at {setup['entry']}; this "
                          "stage's step did not produce the candidate")
            continue
        if stopped is not None:
            record.update(status='not_reached', reason=f'stopped at stage {stopped}')
            continue
        if entry is None or not (entry.get('steps') or entry.get('placement') or entry.get('checks')):
            record.update(status='absent', reason=PARTS_ABSENT if name == 'parts' else
                          f'no declared step or check for stage {name}')
            stopped = number
            continue
        if entry.get('placement') and not current:
            # The placement adapter binds to a captured candidate: review the current design first.
            last_review, command = review(rundir, setup, f'{number}-{name}-binding')
            current = {'run': last_review, 'command': command, 'seal_error': None}
        if entry.get('steps') or entry.get('placement'):
            base = rundir / 'stages' / f'{number}-{name}'
            summaries, ok = run_stage_steps(base, design, entry, setup['scope'], last_review)
            record['receipts'] = [{**s, 'receipt': rq.rel(s['receipt'], rundir)} for s in summaries if 'receipt' in s]
            for summary in summaries:
                if summary.get('status') in ('completed', 'partial') and 'receipt' in summary:
                    receipts.append(Path(summary['receipt']))
            current = False
            if not ok:
                failure = next((s for s in summaries if s.get('status') not in ('completed', 'partial')
                                or s.get('refused') or s.get('adoption_error')
                                or (s.get('routing_verify') or {}).get('error')), summaries[-1] if summaries else {})
                record.update(status='failed', reason='step not adopted: ' + json.dumps(
                    {k: failure.get(k) for k in ('step', 'status', 'exit_code', 'violations', 'refused',
                                                 'adoption_error', 'stopped') if failure.get(k)}, sort_keys=True))
                stopped = number
                continue
        if not entry.get('checks'):
            record.update(status='completed', reason='steps completed and adopted; no check grades this stage')
            continue
        if not current:
            last_review, command = review(rundir, setup, f'{number}-{name}')
            current = {'run': last_review, 'command': command, 'seal_error': sealed(last_review)
                       if (last_review / 'result.json').is_file() else 'review wrote no result.json'}
        record['review'] = {'run': rq.rel(current['run'], rundir), 'command': current['command'],
                            'seal_error': current['seal_error']}
        if current['seal_error']:
            record.update(status='missing_coverage', reason='review not sealed: ' + current['seal_error'])
            stopped = number
            continue
        status, reason, record['checks'] = grade(last_review, entry['checks'])
        binding = produced_binding(last_review, receipts)
        record['graded_design'] = binding
        if status == 'pass' and binding['applicability'] not in ('applicable', 'supplied'):
            status, reason = 'failed', 'graded design is not the last receipt output: ' + json.dumps(binding)
        record.update(status=status, reason=reason)
        if status != 'pass':
            stopped = number
    return stages


# ------------------------------------------------------------------ record

def tool_versions():
    def first_line(argv):
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {'argv': argv, 'version': None, 'reason': f'{type(exc).__name__}: {exc}'}
        # ngspice frames its banner in asterisks; the first line with text is the version.
        lines = [line.strip(' *') for line in (proc.stdout or proc.stderr).splitlines()]
        lines = [line for line in lines if line]
        return {'argv': argv, 'exit_code': proc.returncode, 'version': lines[0] if lines else None}
    return {'python': sys.version.split()[0], 'kicad-cli': first_line(['kicad-cli', 'version']),
            'ngspice': first_line(['ngspice', '--version']), 'bwrap': first_line(['bwrap', '--version'])}


def fab_manifest(rundir, stages):
    """The done check's sealed artifact record and manifest hash, when the fab stage ran."""
    fab = stages[-1]
    if not fab['checks'] or fab['review'] is None:
        return None
    cid = next(iter(fab['checks']))
    run = Path(rundir) / fab['review']['run']
    result = read_json(run / 'checks' / f'{cid}.json')
    artifacts = (result.get('details') or {}).get('artifacts')
    manifest = run / 'work' / cid / 'fab' / 'manifest.json'
    return {'check': cid, 'execution': result.get('execution'), 'outcome': result.get('outcome'),
            'artifacts': artifacts, 'manifest': rq.rel(manifest, rundir) if manifest.is_file() else None,
            'manifest_sha256': digest(manifest) if manifest.is_file() else None}


def bound_paths(rundir, stages):
    """Every file the record binds: receipts, sealed check results, review results and seals."""
    rundir = Path(rundir)
    paths = set()
    for stage in stages:
        paths.update(r['receipt'] for r in stage['receipts'])
        if stage['review']:
            run = rundir / stage['review']['run']
            for name in ('result.json', 'candidate.json', 'work/verification/evidence.json'):
                if (run / name).is_file():
                    paths.add(rq.rel(run / name, rundir))
            paths.update(rq.rel(run / c['path'], rundir) for c in stage['checks'].values() if c)
    for name in ('scope.json', 'scaffold.json'):
        if (rundir / name).is_file():
            paths.add(name)
    return sorted(paths)


def bound_files(rundir, stages):
    return {p: digest(Path(rundir) / p) for p in bound_paths(rundir, stages)}


def write_record(rundir, setup, stages, *, mode, error=None):
    fab = fab_manifest(rundir, stages) if stages else None
    done = (bool(stages) and all(s['status'] in ('pass', 'completed') for s in stages) and
            stages[-1]['status'] == 'pass' and fab is not None and fab['outcome'] == 'pass')
    record = {'schema': RECORD_SCHEMA, 'qualification': QUALIFICATION, 'mode': mode,
              'entry': setup['entry'] if setup else None, 'error': error,
              'agent_ee': rq.git_head(REPO_ROOT), 'tools': tool_versions(),
              'supplied': setup['supplied'] if setup else None,
              'stages': stages, 'fab_manifest': fab, 'done': done, 'done_rule': DONE_RULE,
              'files': bound_files(rundir, stages)}
    record['record_id'] = identity(record)
    write_json(Path(rundir) / RECORD, record)
    return record


def run(rundir, *, reference=None, spec=None, requirement_record=None, kicad_route=rq.DEFAULT_KICAD_ROUTE,
        catalog_checkout=None, parts_manifest=None, omit=()):
    rundir = Path(rundir).absolute()
    if rundir.exists():
        raise FileExistsError(f'{rundir} exists; choose a fresh run directory')
    if sum(x is not None for x in (reference, spec, requirement_record)) != 1:
        raise ValueError('name exactly one of a reference scope, a spec or a requirement record')
    unknown = sorted(set(omit) - set(NAMES))
    if unknown:
        raise ValueError(f'unknown stage to omit: {unknown}')
    rundir.mkdir(parents=True)
    mode = 'reference' if reference else 'generation' if spec else 'requirements'
    try:
        if reference:
            setup = reference_setup(rundir, reference)
        elif spec:
            setup = generation_setup(rundir, spec, kicad_route=kicad_route, catalog_checkout=catalog_checkout,
                                     parts_manifest=parts_manifest)
        else:
            setup = requirements_setup(rundir, requirement_record)
    except (StageRefusal, ValueError, OSError) as exc:
        error = f'{type(exc).__name__}: {exc}'
        stages = [{'number': number, 'stage': name, 'receipt_stage': receipt_stage, 'owner': owner,
                   'status': 'not_reached', 'reason': 'run setup refused: ' + error, 'receipts': [], 'review': None,
                   'checks': {}} for number, name, receipt_stage, owner in STAGES]
        return write_record(rundir, None, stages, mode=mode, error=error)
    for name in omit:  # remove the stage's declared step; its checks cannot run without it
        setup['plan'].pop(name, None)
    setup['supplied']['omitted_stages'] = sorted(omit)
    return write_record(rundir, setup, run_plan(rundir, setup), mode=mode)


# ------------------------------------------------------------------ verification

def verify_record(rundir):
    """Recompute the record: identity, bound hashes, commit, receipts, review seals and statuses."""
    from .stage_receipts import validate_receipt
    rundir = Path(rundir).absolute()
    record = read_json(rundir / RECORD)
    problems = []
    if record.get('schema') != RECORD_SCHEMA:
        raise ValueError('not a rung-1 stage record')
    if identity({k: v for k, v in record.items() if k != 'record_id'}) != record.get('record_id'):
        problems.append('record identity mismatch')
    head = rq.git_head(REPO_ROOT)
    if head['commit'] != record['agent_ee']['commit']:
        problems.append(f"agent-ee commit {head['commit']} differs from the recorded {record['agent_ee']['commit']}")
    elif head['tracked_changes'] != record['agent_ee']['tracked_changes']:
        problems.append('agent-ee tracked changes differ from the recorded working tree')
    for path, sha in record['files'].items():
        if not (rundir / path).is_file() or digest(rundir / path) != sha:
            problems.append(f'bound file changed or missing: {path}')
    if bound_paths(rundir, record['stages']) != sorted(record['files']):
        problems.append('bound file set differs from the stages')
    statuses = []
    runs = {}
    adopted = []
    for stage in record['stages']:
        statuses.append(stage['status'])
        if stage['status'] not in STATUSES:
            problems.append(f"stage {stage['stage']}: unknown status {stage['status']}")
        for receipt in stage['receipts']:
            try:
                value = validate_receipt((rundir / receipt['receipt']).parent)
            except (ValueError, OSError) as exc:
                problems.append(f"stage {stage['stage']}: receipt {receipt['receipt']}: {exc}")
                continue
            if value['status'] != receipt['status'] or value['identity'] != receipt['receipt_identity']:
                problems.append(f"stage {stage['stage']}: receipt {receipt['receipt']} differs from the record")
            if value['status'] in ('completed', 'partial'):
                adopted.append(rundir / receipt['receipt'])
        if stage['status'] == 'completed' and (not stage['receipts'] or any(
                r['status'] not in ('completed', 'partial') for r in stage['receipts'])):
            problems.append(f"stage {stage['stage']}: completed without adoptable receipts")
        if stage['review'] and stage['review']['seal_error'] is None:
            run_dir = rundir / stage['review']['run']
            if run_dir not in runs:
                runs[run_dir] = sealed(run_dir)
            if runs[run_dir]:
                problems.append(f"stage {stage['stage']}: review does not verify: {runs[run_dir]}")
        if stage['status'] == 'pass':
            if not stage['review'] or not stage['checks']:
                problems.append(f"stage {stage['stage']}: pass without a sealed result")
                continue
            run_dir = rundir / stage['review']['run']
            if stage['review']['seal_error'] is not None:
                problems.append(f"stage {stage['stage']}: pass with an unsealed review")
            status, _, checks = grade(run_dir, list(stage['checks']))
            if status != 'pass' or checks != stage['checks']:
                problems.append(f"stage {stage['stage']}: sealed results do not grade pass")
            try:
                binding = produced_binding(run_dir, adopted)
            except (OSError, ValueError, KeyError) as exc:
                binding = {'error': str(exc)}
            if binding != stage.get('graded_design') or binding.get('applicability') not in ('applicable', 'supplied'):
                problems.append(f"stage {stage['stage']}: graded design is not the recorded receipt output")
    if 'pass' in statuses or 'completed' in statuses:
        reached = [s for s in statuses if s != 'not_reached']
        stop = next((i for i, s in enumerate(statuses) if s in ('failed', 'missing_coverage', 'absent')), None)
        if stop is not None and any(s != 'not_reached' for s in statuses[stop + 1:]):
            problems.append('a stage after the stopping stage is not not_reached')
        if not reached:
            problems.append('no stage was reached')
    done = (all(s in ('pass', 'completed') for s in statuses) and statuses[-1] == 'pass' and
            (record.get('fab_manifest') or {}).get('outcome') == 'pass')
    if bool(record['done']) != done:
        problems.append('done flag contradicts the stage statuses')
    return {'verified': not problems, 'problems': problems, 'record_id': record.get('record_id'),
            'commit': record['agent_ee']['commit'], 'statuses': dict(zip(NAMES, statuses))}


# ------------------------------------------------------------------ CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    run_parser = sub.add_parser('run', help='run the fixed stage order on one candidate; no inference')
    run_parser.add_argument('--out', required=True, type=Path)
    source = run_parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--reference', type=Path, help='scope of a supplied finished board; enters at verification')
    source.add_argument('--spec', type=Path, help='rung-1 spec; rung1_qualified preparation, enters at schematic')
    source.add_argument('--requirement-record', type=Path, nargs='?', const=REQUIREMENT_RECORD,
                        help='agent-ee.requirement-record/1 beside its spec; enters at requirements '
                             f'(default {REQUIREMENT_RECORD.relative_to(REPO_ROOT)})')
    run_parser.add_argument('--kicad-route', type=Path, default=rq.DEFAULT_KICAD_ROUTE)
    run_parser.add_argument('--catalog-checkout', type=Path)
    run_parser.add_argument('--parts-manifest', type=Path)
    run_parser.add_argument('--omit-step', action='append', default=[], choices=NAMES, metavar='STAGE',
                            help='remove a stage\'s declared step (repeatable); that stage is absent')
    verify_parser = sub.add_parser('verify', help='recompute a stage record against this agent-ee commit')
    verify_parser.add_argument('rundir', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'verify':
        try:
            result = verify_record(args.rundir)
        except (OSError, ValueError, KeyError) as exc:
            parser.exit(2, f'rung1_stages verify refused: {exc}\n')
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result['verified'] else 1
    try:
        record = run(args.out, reference=args.reference, spec=args.spec, requirement_record=args.requirement_record,
                     kicad_route=args.kicad_route.absolute(), catalog_checkout=args.catalog_checkout,
                     parts_manifest=args.parts_manifest, omit=args.omit_step)
    except (FileExistsError, OSError, ValueError) as exc:
        parser.exit(2, f'rung1_stages refused: {exc}\n')
    print(json.dumps({'mode': record['mode'], 'error': record['error'], 'done': record['done'],
                      'stages': {s['stage']: s['status'] for s in record['stages']}}))
    return 0 if record['done'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
