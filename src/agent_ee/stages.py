"""Opt-in ordered gates; presence witnesses are coordinator facts, not tool passes."""

from .storage import identity

STAGE_ORDER = ('schematic', 'placement', 'routing', 'verification', 'fab')
PRESENCE_VERSIONS = {'kicad_sch': {'20250114', '20260306'},
                     'kicad_pcb': {'20250114', '20260101', '20260206'}}


def declaration(scope, files=None):
    value = scope.get('stages')
    if value is None:
        if any('stage' in c for c in scope.get('checks', [])):
            raise ValueError('check stage requires agent-ee.stages/1 declaration')
        return None
    if (scope.get('schema') not in ('agent-ee.scope/2', 'agent-ee.scope/3') or
            not isinstance(value, dict) or set(value) != {'schema', 'order', 'subjects', 'finding_retry_limit'} or
            value['schema'] != 'agent-ee.stages/1'):
        raise ValueError('expected immutable agent-ee.stages/1 declaration')
    order = value['order']
    if (not isinstance(order, list) or not order or
            any(s not in STAGE_ORDER for s in order) or
            order != [s for s in STAGE_ORDER if s in order]):
        raise ValueError('stages must be a unique ordered generation-stage subsequence')
    limit = value['finding_retry_limit']
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError('finding_retry_limit must be between 1 and 100')
    specs = scope['checks']
    if any(c.get('stage') not in order for c in specs):
        raise ValueError('every staged check must name a declared stage')
    indices = [order.index(c['stage']) for c in specs]
    if indices != sorted(indices) or set(c['stage'] for c in specs) != set(order):
        raise ValueError('checks must cover every stage in stage order')
    subjects = value['subjects']
    if not isinstance(subjects, dict) or not subjects or not set(subjects) <= set(order):
        raise ValueError('stages require expected-content subjects for declared stages')
    for subject in subjects.values():
        if not isinstance(subject, dict) or subject.get('kind') not in ('symbols', 'footprints', 'tracks'):
            raise ValueError('stage subject requires a supported native kind')
        fields = {'path', 'kind', 'minimum'} if subject['kind'] == 'tracks' else {'path', 'kind', 'references'}
        if set(subject) != fields:
            raise ValueError('stage subject fields do not match native kind')
        path = scope.get('schematic' if subject['kind'] == 'symbols' else 'board')
        if subject['path'] != path or (files is not None and path not in files):
            raise ValueError('stage subject must name its captured native design')
        if subject['kind'] == 'tracks':
            minimum = subject['minimum']
            if isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= 1000000:
                raise ValueError('expected track minimum must be between 1 and 1000000')
            continue
        refs = subject['references']
        if (not isinstance(refs, list) or not refs or
                any(not isinstance(r, str) or not r.strip() for r in refs) or len(set(refs)) != len(refs)):
            raise ValueError('expected references must be nonempty unique strings')
    return value


def projection(checks, scope):
    config = declaration(scope)
    stages = {c['id']: c['stage'] for c in scope['checks']}
    failed = {c['id'] for c in checks if c['outcome'] == 'defect'}
    earliest = next((s for s in config['order'] if any(stages[c] == s for c in failed)), None)
    return {'stage_policy_id': identity(config), 'earliest_failing_stage': earliest,
            'stage_failures': {s: sorted(c for c in failed if stages[c] == s) for s in config['order']},
            'blocked_checks': sorted(c['id'] for c in checks
                                    if c.get('details', {}).get('reason') == 'stage_blocked')}


def progress(previous, current):
    """Only the previously earliest failing stage can earn stage progress.

    Its failed checks shrink, or the same checks report a strict subset of the
    previous finding identities: a cleared finding is progress, a new one is not.
    """
    stage = previous['earliest_failing_stage']
    if stage is None:
        return False
    before, after = set(previous['stage_failures'][stage]), set(current['stage_failures'][stage])
    return after < before or (after == before and current['earliest_failing_stage'] == stage and
                              set(current['recurring_findings']) < set(previous.get('recurring_findings', [])))


def native_references(path, kind):
    """Read only explicit native Reference properties; hierarchical sheets unsupported."""
    from pathlib import Path
    from .symbol_library import LibraryError, children, parse_sexpr
    root = parse_sexpr(Path(path).read_bytes())
    expected_root, tag = ('kicad_sch', 'symbol') if kind == 'symbols' else ('kicad_pcb', 'footprint')
    if root[0] != expected_root or len(children(root, 'version')) != 1:
        raise LibraryError('expected versioned native stage subject')
    version = children(root, 'version')[0]
    if len(version) != 2 or version[1] not in PRESENCE_VERSIONS[expected_root]:
        raise LibraryError('unsupported native stage presence version')
    if children(root, 'sheet'):
        raise LibraryError('hierarchical stage presence is not supported')
    if kind == 'tracks':
        return sorted(f'{tag}:{index}' for tag in ('segment', 'arc')
                      for index, _ in enumerate(children(root, tag)))
    references = []
    for node in children(root, tag):
        props = [p for p in children(node, 'property') if len(p) >= 2 and p[1] == 'Reference']
        if len(props) != 1 or len(props[0]) < 3 or not isinstance(props[0][2], str) or not props[0][2].strip():
            raise LibraryError('native stage subject has missing/ambiguous Reference')
        if kind == 'symbols' and children(node, 'in_bom') == [['in_bom', 'no']] and children(node, 'on_board') == [['on_board', 'no']]:
            continue
        if kind == 'symbols' and props[0][2].startswith('#'):
            # KiCad's netlist exporter omits virtual references (power symbols, PWR_FLAG)
            # whatever their in_bom/on_board flags; native_probe still compares the result.
            continue
        references.append(props[0][2])
    if len(set(references)) != len(references):
        raise LibraryError('native stage subject has duplicate references')
    return sorted(references)


def subject_witness(manifest, baseline, stage, observed):
    subject = manifest['scope']['stages']['subjects'][stage]
    expected = subject['minimum'] if subject['kind'] == 'tracks' else sorted(subject['references'])
    missing = max(0, expected - len(observed)) if subject['kind'] == 'tracks' else sorted(set(expected) - set(observed))
    return {'schema': 'agent-ee.stage-subject/1', 'candidate_id': manifest['candidate_id'],
            'baseline_id': baseline['baseline_id'], 'stage': stage,
            'path': subject['path'], 'sha256': manifest['files'][subject['path']],
            'kind': subject['kind'], 'expected': expected, 'observed': observed, 'missing': missing}


def before_check(ctx, prior):
    """Return whether the handler may run, retaining actual probe and gate artifacts."""
    from .checks import CheckError
    from . import executor
    from .storage import read_json, write_json
    config = declaration(ctx.scope, ctx.manifest['files'])
    if config is None:
        return True
    stage = ctx.spec['stage']
    earlier = {c['id'] for c in ctx.scope['checks']
               if config['order'].index(c['stage']) < config['order'].index(stage)}
    blockers = [c for c in prior if c['id'] in earlier and
                (c['execution'] != 'ok' or c['outcome'] not in ('pass', 'info'))]
    incomplete = any(c['execution'] != 'ok' or c['outcome'] == 'unknown' for c in blockers)
    subject = config['subjects'].get(stage)
    if blockers and (incomplete or subject is None):
        return blocked_result(ctx, blockers)
    if subject is None:
        return True
    work = ctx.work / 'stage-subject'
    work.mkdir()
    rec = executor.execute(ctx.inputs, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.stages',
        ctx.candidate_path(subject['path']), subject['kind'], '/work/references.json'],
        timeout=min(60, ctx.remaining()))
    ctx.evidence('stage-subject/execution.json', 'stage-subject/console.log',
                 'stage-subject/native-netlist.xml', 'stage-subject/native-observation.json')
    if rec['execution'] != 'ok':
        raise CheckError(rec['execution'], 'stage subject probe did not complete')
    try:
        observed = read_json(work / 'references.json')
    except (OSError, ValueError) as exc:
        raise CheckError('malformed', 'stage subject probe output missing or malformed') from exc
    if (not isinstance(observed, list) or any(not isinstance(r, str) for r in observed) or
            observed != sorted(set(observed))):
        raise CheckError('malformed', 'stage subject probe returned malformed references')
    ctx.evidence('stage-subject/references.json')
    witness = subject_witness(ctx.manifest, read_json(ctx.run_dir / 'baseline.json'), stage, observed)
    write_json(ctx.work / 'stage-subject.json', witness)
    ctx.evidence('stage-subject.json')
    if witness['missing']:
        ctx.result.update(execution='ok', outcome='defect', summary=f'{stage}: expected native content absent')
        ctx.result['details'].update(reason='stage_not_reached', stage=stage,
            gate_findings=[{'check_id': ctx.cid, 'entity': ref, 'kind': 'stage_not_reached'}
                           for ref in ([subject['path']] if subject['kind'] == 'tracks' else witness['missing'])])
        return False
    return blocked_result(ctx, blockers) if blockers else True


def blocked_result(ctx, blockers):
    from .storage import read_json, write_json
    incomplete = any(c['execution'] != 'ok' or c['outcome'] == 'unknown' for c in blockers)
    stage = ctx.spec['stage']
    witness = {'schema': 'agent-ee.stage-gate/1', 'candidate_id': ctx.manifest['candidate_id'],
               'baseline_id': read_json(ctx.run_dir / 'baseline.json')['baseline_id'],
               'stage': stage, 'blockers': {c['id']: identity(c) for c in blockers}}
    write_json(ctx.work / 'stage-gate.json', witness)
    ctx.evidence('stage-gate.json')
    ctx.result.update(execution='not_run' if incomplete else 'ok',
                      outcome='unknown' if incomplete else 'defect',
                      summary=f'{stage} gate blocked by earlier checks')
    ctx.result['details'].update(reason='stage_blocked', stage=stage)
    return False


def verify_gate(run, check, manifest, baseline):
    """Reconstruct special gate verdicts from immutable declarations and captured facts."""
    from pathlib import Path
    from .storage import read_json
    reason = check.get('details', {}).get('reason')
    if reason not in ('stage_not_reached', 'stage_blocked'):
        return
    config = declaration(manifest['scope'], manifest['files'])
    if config is None:
        raise ValueError('stage verdict requires an immutable stage declaration')
    spec = next(c for c in manifest['scope']['checks'] if c['id'] == check['id'])
    stage = spec['stage']
    work = f"work/{check['id']}"
    if reason == 'stage_not_reached':
        subject = config['subjects'][stage]
        observed = native_references(Path(run) / 'inputs' / subject['path'], subject['kind'])
        expected = subject_witness(manifest, baseline, stage, observed)
        probe = work + '/stage-subject/'
        required = [probe + name for name in ('execution.json', 'console.log', 'references.json', 'native-observation.json')]
        execution = read_json(Path(run) / probe / 'execution.json')
        argv = ['/usr/bin/python3', '-B', '-m', 'agent_ee.stages',
                '/candidate/' + subject['path'], subject['kind'], '/work/references.json']
        native = read_json(Path(run) / probe / 'native-observation.json')
        if (execution.get('execution') != 'ok' or execution.get('exit_code') != 0 or
                execution.get('argv') != argv or native.get('native_references') != observed or
                read_json(Path(run) / probe / 'references.json') != observed):
            raise ValueError('stage absence lacks successful native observation')
        if subject['kind'] == 'symbols':
            import xml.etree.ElementTree as ET
            required.append(probe + 'native-netlist.xml')
            exported = ET.parse(Path(run) / probe / 'native-netlist.xml').getroot()
            if (exported.tag != 'export' or exported.find('components') is None or
                    exported.find('design') is None or
                    sorted(c.get('ref') for c in exported.findall('./components/comp')) != observed):
                raise ValueError('stage native netlist contradicts presence witness')
        if not set(required) <= set(check['evidence']):
            raise ValueError('stage verdict omits native probe evidence')
        rel = work + '/stage-subject.json'
        if (not expected['missing'] or check['execution'] != 'ok' or check['outcome'] != 'defect' or
                read_json(Path(run) / rel) != expected):
            raise ValueError('stage_not_reached contradicts captured expected-content witness')
    else:
        earlier = [c for c in manifest['scope']['checks']
                   if config['order'].index(c['stage']) < config['order'].index(stage)]
        results = [read_json(Path(run) / 'checks' / (c['id'] + '.json')) for c in earlier]
        blockers = [c for c in results if c['execution'] != 'ok' or c['outcome'] not in ('pass', 'info')]
        incomplete = any(c['execution'] != 'ok' or c['outcome'] == 'unknown' for c in blockers)
        expected = {'schema': 'agent-ee.stage-gate/1', 'candidate_id': manifest['candidate_id'],
                    'baseline_id': baseline['baseline_id'], 'stage': stage,
                    'blockers': {c['id']: identity(c) for c in blockers}}
        rel = work + '/stage-gate.json'
        if (not blockers or read_json(Path(run) / rel) != expected or
                check['execution'] != ('not_run' if incomplete else 'ok') or
                check['outcome'] != ('unknown' if incomplete else 'defect')):
            raise ValueError('stage gate contradicts earlier check results')
    if rel not in check['evidence'] or check['details'].get('stage') != stage:
        raise ValueError('stage verdict omits its coordinator witness')


def validate_gate_findings(check):
    import json
    details = check.get('details', {})
    if 'gate_findings' not in details:
        return
    values = details['gate_findings']
    if not isinstance(values, list):
        raise ValueError('gate findings must be a list')
    seen = set()
    for item in values:
        if (not isinstance(item, dict) or not {'check_id', 'entity', 'kind'} <= set(item) or
                set(item) - {'check_id', 'entity', 'kind', 'suggested_fix'} or
                item['check_id'] != check['id'] or
                any(not isinstance(item[k], str) or not item[k].strip() for k in ('entity', 'kind'))):
            raise ValueError('malformed gate finding identity')
        key = (item['entity'], item['kind'])
        if key in seen:
            raise ValueError('duplicate gate finding identity')
        seen.add(key)
        if 'suggested_fix' in item:
            fix = item['suggested_fix']
            if (not isinstance(fix, dict) or set(fix) != {'op', 'params'} or
                    not isinstance(fix['op'], str) or not fix['op'].strip() or
                    not isinstance(fix['params'], dict)):
                raise ValueError('malformed gate finding suggested_fix')
            try:
                json.dumps(fix['params'], allow_nan=False)
            except (ValueError, TypeError) as exc:
                raise ValueError('gate finding parameters must be finite JSON') from exc


def project_findings(check_id, entries):
    """Deduplicated gate findings from (entity, kind[, suggested_fix]); None when any defect lacks an identity."""
    findings, seen = [], set()
    for entity, kind, *fix in entries:
        if any(not isinstance(v, str) or not v.strip() for v in (entity, kind)):
            return None
        if (entity, kind) not in seen:
            seen.add((entity, kind))
            findings.append({'check_id': check_id, 'entity': entity, 'kind': kind,
                             **({'suggested_fix': fix[0]} if fix and fix[0] is not None else {})})
    return findings or None


def finding_keys(checks):
    keys = set()
    for check in checks:
        validate_gate_findings(check)
        if check['execution'] != 'ok' or check['outcome'] != 'defect':
            continue
        if check.get('details', {}).get('reason') == 'stage_blocked':
            continue
        findings = check.get('details', {}).get('gate_findings')
        if findings:
            keys.update(identity({k: item[k] for k in ('check_id', 'entity', 'kind')}) for item in findings)
        else:
            keys.add(identity({'check_id': check['id']}))
    return sorted(keys)


def native_probe(path, kind):
    """Require the pinned native reader to agree before attesting absence."""
    import subprocess
    import xml.etree.ElementTree as ET
    from .storage import write_json
    observed = native_references(path, kind)
    if kind == 'symbols':
        argv = ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                '-o', '/work/native-netlist.xml', path]
        subprocess.run(argv, check=True)
        root = ET.parse('/work/native-netlist.xml').getroot()
        if root.tag != 'export' or root.find('components') is None or root.find('design') is None:
            raise ValueError('native stage netlist is incomplete')
        native = sorted(c.get('ref') for c in root.findall('./components/comp'))
        receipt = {'argv': argv, 'native_references': native}
    else:
        import pcbnew
        board = pcbnew.LoadBoard(path)
        if board is None:
            raise ValueError('native stage board did not load')
        if kind == 'tracks':
            native = sorted(f'{tag}:{index}' for tag, cls in (('segment', 'PCB_TRACK'), ('arc', 'PCB_ARC'))
                            for index, _ in enumerate([t for t in board.GetTracks() if t.GetClass() == cls]))
        else:
            native = sorted(fp.GetReference() for fp in board.GetFootprints())
        receipt = {'reader': 'pcbnew.LoadBoard', 'version': pcbnew.Version(), 'native_references': native}
    write_json('/work/native-observation.json', receipt)
    if observed != native:
        raise ValueError('bounded inventory disagrees with native reader; coverage unsupported')
    return observed


if __name__ == '__main__':
    import sys
    from .storage import write_json
    write_json(sys.argv[3], native_probe(sys.argv[1], sys.argv[2]))
