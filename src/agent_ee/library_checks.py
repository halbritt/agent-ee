"""Coordinator adapter for captured library checks; offline helpers stay pure."""
from pathlib import Path

from .candidate import safe_relative
from .footprint_library import (_members, _object, load_document, validate_declaration, validate_declarations)
from .storage import canonical
from .native_binding import PREDICATE as NATIVE_PREDICATE


def capability(params, files, inputs, scope=None, check_id=None):
    """Declared-only by default; an ordered ``netlist_check`` binds the native design."""
    native = isinstance(params, dict) and 'netlist_check' in params
    declared = isinstance(params, dict) and 'declarations' in params
    _object(params, ('manifest', 'declaration', 'assembly_intent') + (('netlist_check',) if native else ())
            + (('declarations',) if declared else ()))
    if native:
        specs = (scope or {}).get('checks', [])
        ids = [s.get('id') for s in specs]
        prior = next((s for s in specs if s.get('id') == params['netlist_check']), None)
        if (prior is None or prior.get('kind') != 'netlist' or check_id not in ids
                or ids.index(prior['id']) >= ids.index(check_id)):
            raise ValueError('unresolved/forward/wrong-kind native netlist prerequisite')
    if inputs is None:
        raise ValueError('captured inputs required for library capability')
    manifest_path = str(safe_relative(params['manifest']))
    declaration_path = str(safe_relative(params['declaration']))
    if manifest_path not in files or declaration_path not in files:
        raise ValueError('library manifest and declaration must be captured')
    manifest = load_document(Path(inputs) / manifest_path)
    declaration = load_document(Path(inputs) / declaration_path)
    members = _members(manifest)
    validate_declaration(declaration)
    if any(c['assembly'] == 'mixed' for c in declaration['components']):
        raise ValueError('mixed assembly intent is not qualified')
    policy = params['assembly_intent']
    if not isinstance(policy, list) or not policy:
        raise ValueError('explicit immutable assembly intent required')
    refs = set()
    for item in policy:
        _object(item, ('reference', 'assembly', 'connector'))
        if not isinstance(item['reference'], str) or not item['reference'] or item['reference'] in refs:
            raise ValueError('unique assembly-intent references required')
        refs.add(item['reference'])
    actual = [dict(reference=c['reference'], assembly=c['assembly'], connector=c['connector'])
              for c in declaration['components']]
    if sorted(actual, key=canonical) != sorted(policy, key=canonical):
        raise ValueError('candidate assembly declarations differ from immutable intent')
    paths = [manifest_path] + [m['path'] for _, m in members]
    if declared:
        # Owner pad/assembly/pinless declarations are frozen scope inputs, never candidate data.
        declarations_path = str(safe_relative(params['declarations']))
        if declarations_path not in files:
            raise ValueError('footprint declarations must be captured')
        validate_declarations(load_document(Path(inputs) / declarations_path))
        paths.append(declarations_path)
    for lib in declaration['libraries']:
        _object(lib, ('name', 'path', 'sha256'))
        paths.append(str(safe_relative(lib['path'])))
    if any(p not in files for p in paths):
        raise ValueError('complete library closure must be captured')
    return dict(immutable_inputs=sorted(set(paths)),
                predicates=[NATIVE_PREDICATE if native else 'footprint:assignment-parity/1'])


def check_footprint_assignment(ctx):
    from .checks import CheckError
    try:
        capability(ctx.params, ctx.manifest['files'], ctx.inputs, ctx.scope, ctx.cid)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    if 'netlist_check' in ctx.params:
        return _check_native(ctx)
    try:
        ctx.execute(['/usr/bin/python3', '-m', 'agent_ee.footprint_library',
                 '/candidate/' + ctx.params['manifest'], '--captures', '/candidate',
                 '--assignments', '/candidate/' + ctx.params['declaration'], *_declarations(ctx),
                     '--out', '/work/assignments.json'], 60)
    finally:
        ctx.evidence('assignments.json')
    report = ctx.load_json('assignments.json', 'footprint assignment')
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
                      summary='captured symbol-to-footprint assignment and numbered pad parity')
    ctx.result['details'].update(report=report)
    ctx.result['location'] = ', '.join(a['reference'] + ':' + a['footprint']
        for a in report['assignments'] if a['status'] == 'defect')
    ctx.result['limitations'].append('Declared component list only; no schematic binding, package selection, '
        'land-pattern geometry qualification, datasheet conformance or manufacturing approval.')


def _declarations(ctx):
    return ['--declarations', '/candidate/' + ctx.params['declarations']] if 'declarations' in ctx.params else []


def _check_native(ctx):
    """Sandboxed binding of the retained native XML; the coordinator verifies identity."""
    from .checks import CheckError, verified_hierarchy
    from .datasheet_facts import prerequisite
    from .storage import digest, read_json
    cid = ctx.params['netlist_check']
    ctx.result['details']['evaluated_predicates'] = []
    producer_path = ctx.run_dir / 'checks' / f'{cid}.json'
    if producer_path.is_file() and read_json(producer_path).get('execution') != 'ok':
        raise CheckError('failed', 'native netlist producer did not complete successfully')
    xml = prerequisite(ctx, cid, 'netlist', 'design.xml')
    producer = read_json(producer_path)
    sheets, expected = verified_hierarchy(ctx)
    if producer.get('details', {}).get('sheet_instances') != expected:
        raise CheckError('malformed', 'native producer hierarchy coverage differs from the captured schematic')
    try:
        ctx.execute(['/usr/bin/python3', '-m', 'agent_ee.native_binding',
                     '/candidate/' + ctx.params['manifest'], '--captures', '/candidate',
                     '--assignments', '/candidate/' + ctx.params['declaration'], *_declarations(ctx),
                     '--native-xml', '/evidence/netlist/design.xml', '--schematic', *sheets,
                     '--out', '/work/native-assignments.json'], 60,
                    extra_ro=[(xml, '/evidence/netlist/design.xml')])
    finally:
        ctx.evidence('native-assignments.json')
    report = ctx.load_json('native-assignments.json', 'native footprint binding')
    native_sha = digest(xml)
    if report.get('native_xml_sha256') != native_sha:
        raise CheckError('malformed', 'binding report does not identify the retained native XML')
    ctx.result['details'].update(report=report, netlist_check=cid, native_xml_sha256=native_sha,
                                 candidate_id=ctx.manifest['candidate_id'])
    ctx.result['location'] = ', '.join(b['reference'] for b in report['bindings']
                                       if b['status'] in ('defect', 'unknown'))
    ctx.result['limitations'].append('Native board components bound to declared assembly policy and '
        'captured libraries only; no package selection, land-pattern geometry, datasheet conformance, '
        'placement or manufacturing approval. Exporter rules are qualified for KiCad 10.0.7.')
    if report.get('execution') != 'ok':
        execution = report.get('execution')
        raise CheckError(execution if execution in ('failed', 'malformed', 'unsupported') else 'malformed',
                         f'native footprint binding {execution}; see native-assignments.json')
    if len(report['native_sheets']) != expected:
        raise CheckError('malformed', 'native XML hierarchy differs from producer coverage')
    ctx.result.update(execution='ok', outcome=report['outcome'],
                      summary=f"native-bound footprint assignment: {report['outcome']}")
    ctx.result['details']['evaluated_predicates'] = [NATIVE_PREDICATE]
