"""Qualification of retained builds from sealed typed evidence.

Assessment owns its record and report projection; build custody consumes them.
"""
import copy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import shutil
import uuid

from . import build_plan, requirement_map
from .catalog import strict_json
from .stage_receipts import _relative, tree_identity
from .storage import digest, identity, read_json, write_json

TYPED_SCHEMA = build_plan.TYPED_SCHEMA
REPORT_SCHEMA = 'agent-ee.build-report/1'
QUALIFICATION_SCHEMA = 'agent-ee.qualification/1'
CLAIM = ('software file obligations only; delivery, judgment, supplier and physical '
         'obligations are listed under coverage with their own status')
DIMENSIONS = ('checks', 'file_release', 'operational', 'engineering_review', 'judgments', 'supplier', 'physical')
WITNESS_CHECKS = ('source_citations_resolved', 'applicability_recomputed', 'conclusion_consistent_with_bound_checks')
STAGES = ('requirements', 'parts', 'scaffold', 'schematic', 'placement-routing', 'artwork', 'package')
MANDATORY = {'erc', 'drc', 'netlist', 'geometry', 'parts_subset', 'placement',
             'board_envelope', 'fab_profile', 'assembly_readiness', 'fab_package'}
_inventory = requirement_map.input_inventory


class QualificationError(ValueError):
    """A current claim cannot be reconstructed from the retained evidence."""
    def __init__(self, message, *, integrity=False):
        self.integrity = integrity
        super().__init__(message)



def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('build declaration fields differ from ' + ', '.join(required))



def _retained_path(root, relative):
    path = root / _relative(relative, 'retained build path')
    if path == root or not path.resolve().is_relative_to(root):
        raise ValueError('retained build path escapes its run')
    for component in [path, *path.parents]:
        if component == root:
            break
        if component.is_symlink():
            raise ValueError('retained build path contains a link')
    return path



def manufacturing_coverage(plan, scope, accounting):
    from .baseline import generation_marker
    missing = MANDATORY - {check['kind'] for check in scope['checks']}
    if missing:
        raise ValueError('manufacturing coverage missing: ' + ', '.join(sorted(missing)))
    unresolved = [r['id'] for r in accounting if r['mode'] == 'limitation']
    if unresolved:
        raise ValueError('required engineering coverage remains unresolved: ' + ', '.join(unresolved))
    if generation_marker(scope) is None:
        raise ValueError('manufacturing build requires the ordinary generation done gate')
    if 'envelope' not in scope:
        raise ValueError('manufacturing build requires captured envelope acceptance')
    if scope['envelope']['path'] != plan['envelope'] or scope['envelope']['profile'] is None:
        raise ValueError('manufacturing checks must bind the generated envelope and its selected profile')
    memberships = [c for c in scope['checks'] if c['kind'] == 'parts_subset']
    if not any(c['params']['manifest'] == plan['parts']['manifest'] for c in memberships):
        raise ValueError('native membership checks must bind the produced parts manifest')
    if 'planes' in plan['placement']:
        bound = [link['path'] for c in scope['checks'] if c['kind'] == 'drc'
                 for link in c.get('params', {}).get('plane_plans', [])]
        bound += [link['plan']['path'] for c in scope['checks'] if c['kind'] == 'drc'
                  for link in c.get('params', {}).get('plane_obligations', [])]
        if plan['placement']['planes'] not in bound:
            raise ValueError('manufacturing plane checks must bind the generated plane plan')



def package_artifacts(result, scope, candidate):
    """Replay the existing file-package predicate from verified check results."""
    from .baseline import compile_baseline
    baseline = compile_baseline(scope, _inventory(candidate), candidate)
    required = {cid for row in baseline['coverage']['requirements'].values() for cid in row['acceptance_checks']}
    checks = {check['id']: check for check in result['checks']}
    failures = [cid for cid in required if cid not in checks or
                (checks[cid]['execution'], checks[cid]['outcome']) != ('ok', 'pass')]
    failures += [cid for cid, check in checks.items() if check['execution'] != 'ok' or
                 check['outcome'] not in ('pass', 'info')]
    if failures:
        raise ValueError('required final checks did not pass: ' + ', '.join(sorted(set(failures))))
    packages = [checks[c['id']] for c in scope['checks'] if c['kind'] == 'fab_package']
    if len(packages) != 1 or packages[0]['outcome'] != 'pass':
        raise ValueError('exactly one passing final manufacturing package is required')
    artifacts = packages[0].get('details', {}).get('artifacts')
    if not artifacts or artifacts['board_sha256'] != digest(candidate / scope['board']):
        raise ValueError('manufacturing package is not bound to the final native board')
    return artifacts



def _record(mode, dimensions, *, claims_withheld=True, coverage=None, methods=None,
            judgments=None, rules=None, decision=None, package_records=None, summary=None):
    """Construct the one qualification record shape for every assessment outcome."""
    return dict(schema=QUALIFICATION_SCHEMA, mode=mode, claims_withheld=claims_withheld,
        accepted=dimensions['file_release']['status'] == 'pass', claim=CLAIM, dimensions=dimensions,
        coverage={} if coverage is None else coverage, methods=[] if methods is None else methods,
        judgments=[] if judgments is None else judgments, rules=[] if rules is None else rules,
        decision={} if decision is None else decision,
        package_records=dict(roundtrip='pending', orientation='pending') if package_records is None else package_records,
        summary=dict(pending_required=[]) if summary is None else summary, related_runs=[])


def summary(out, record):
    """Project a persisted qualification record into the build or review report."""
    return dict(path='qualification.json', sha256=digest(Path(out) / 'qualification.json'),
        mode=record['mode'], dimensions={name: row['status'] for name, row in record['dimensions'].items()})


def _publish(root, record):
    target = _retained_path(root, 'qualification.json')
    temporary = root / ('.qualification-' + uuid.uuid4().hex)
    try:
        write_json(temporary, record)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return record


def _dimension(status='not_assessed', *, binding=None, evidence=(), reason=None):
    row = dict(status=status, binding=dict(binding) if binding is not None else dict(
        board_sha256=None, scope_sha256=None, stage=None), evidence=copy.deepcopy(list(evidence)))
    if reason:
        row['reason'] = reason
    return row


def _snapshot(root):
    report = strict_json(_retained_path(root, 'build-report.json').read_bytes())
    if report.get('schema') != REPORT_SCHEMA or report.get('identity') != identity(report['inputs']):
        raise QualificationError('build identity differs from captured inputs', integrity=True)
    if _inventory(_retained_path(root, 'source')) != report['inputs']['source']:
        raise QualificationError('retained build source identity changed', integrity=True)
    completed = report['completed']
    if len(completed) > len(STAGES):
        raise QualificationError('completed stage inventory is invalid')
    for index, row in enumerate(completed):
        directory = _retained_path(root, row['directory'])
        candidate = _retained_path(root, row['candidate'])
        if (row['stage'] != STAGES[index] or candidate == directory or not candidate.is_relative_to(directory)
                or tree_identity(directory) != row['tree']):
            raise QualificationError('completed stage identity changed: ' + row['stage'], integrity=True)
    if completed and report.get('candidate') != completed[-1]['candidate']:
        raise QualificationError('candidate differs from the last completed stage')
    return report


def _plan(root, report):
    path = _retained_path(root, 'build-plan.json')
    if not path.exists():
        return None
    if digest(path) != report['inputs']['plan_sha256']:
        raise QualificationError('captured build plan identity changed', integrity=True)
    return build_plan.validate_plan(strict_json(path.read_bytes()))


def verify_retained(out):
    """Verify retained custody independently of a proposed resume invocation."""
    root = Path(out)
    try:
        report = _snapshot(root)
        plan = _plan(root, report)
        if plan is None and 'qualification' in report:
            raise ValueError('captured build plan is missing from a qualified run')
        if report.get('accepted') and len(report['completed']) != len(STAGES):
            raise ValueError('accepted build has no complete stage inventory')
        if 'judgment_inventory' in report:
            frozen = report['judgment_inventory']
            path = _retained_path(root, frozen['path'])
            if digest(path) != frozen['sha256'] or _inventory(path.parent / 'judgments') != read_json(path):
                raise ValueError('retained judgment inventory changed after freeze')
        return plan
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise QualificationError(str(exc), integrity=True) from exc


def judgment_inventory(source, plan):
    if 'judgments' not in plan:
        return {}
    directory = _retained_path(source, plan['judgments'])
    if not directory.exists():
        return {}
    return _inventory(directory)


def applicability_facts(candidate, plan):
    envelope = _retained_path(candidate, plan['envelope'])
    raw = strict_json(envelope.read_bytes())
    stackup = raw['stackup']
    return dict(envelope_sha256=digest(envelope),
        board_thickness_mm=stackup.get('nominal_thickness_mm', stackup.get('thickness_mm')),
        profile_sha256=raw.get('fab_profile', {}).get('sha256'),
        layer_count=raw.get('layer_count'), max_component_height_mm=raw.get('max_component_height_mm'))


def freeze_judgments(source, candidate, plan, stage):
    """Copy authored records once, before the package stage is sealed."""
    inventory = judgment_inventory(source, plan)
    target = stage / 'judgments'
    target.mkdir()
    if inventory:
        shutil.copytree(_retained_path(source, plan['judgments']), target, dirs_exist_ok=True)
    if _inventory(target) != inventory or judgment_inventory(source, plan) != inventory:
        raise QualificationError('judgment inputs changed while freezing package')
    write_json(stage / 'judgment-inventory.json', inventory)
    write_json(stage / 'applicability-inventory.json', dict(values=applicability_facts(candidate, plan),
                                                        inputs=_inventory(candidate)))


def verify_judgment_source(source, plan, stage):
    if judgment_inventory(source, plan) != read_json(stage / 'judgment-inventory.json'):
        raise QualificationError('judgment inputs changed after package')


def _evidence(root, path):
    relative = str(path.relative_to(root))
    path = _retained_path(root, relative)
    return dict(path=relative, sha256=digest(path))


def _operational(report):
    if len(report['completed']) != len(STAGES):
        return _dimension()
    failed = {}
    completed = {row['stage']: row for row in report['completed']}
    for attempt in report['attempts']:
        if attempt.get('identity') != report['identity'] or attempt['stage'] not in completed:
            continue
        if attempt['status'] in ('interrupted', 'failed'):
            failed[attempt['stage']] = attempt
        elif (attempt['status'] == 'completed' and attempt['stage'] in failed
                and attempt.get('directory') == completed[attempt['stage']]['directory']
                and failed[attempt['stage']].get('directory') != attempt['directory']):
            return dict(_dimension('pass', evidence=[dict(path=row['directory'], sha256=row['tree']['sha256'],
                kind='sealed_stage_tree') for row in report['completed']]),
                clean_execution=[row['tree']['sha256'] for row in report['completed']],
                resume=dict(interrupted_attempt=failed[attempt['stage']], resumed_attempt=attempt,
                            stage=attempt['stage'], identity=report['identity']))
    return _dimension('pending', reason='No failed/interrupted stage followed by completion under this identity')


def _checked_review(root, report):
    from .cli import verify
    candidates = list(reversed(report['completed']))
    if len(report['completed']) == len(STAGES) - 1:
        for attempt in reversed(report['attempts']):
            if (attempt['stage'] == 'package' and attempt['status'] in ('failed', 'interrupted')
                    and attempt.get('identity') == report['identity']):
                candidates.insert(0, dict(stage='package', directory=attempt['directory'],
                                         candidate=attempt['directory'] + '/candidate'))
                break
    for stage in candidates:
        directory = _retained_path(root, stage['directory'])
        review = directory / 'review'
        if not (review / 'result.json').exists():
            continue
        result_sha256 = digest(review / 'result.json')
        result = verify(review)
        if digest(review / 'result.json') != result_sha256:
            raise QualificationError('Ordinary review changed during verification', integrity=True)
        manifest = read_json(review / 'candidate.json')
        scope = manifest['scope']
        candidate = _retained_path(root, stage['candidate'])
        board_hash = manifest['files'].get(scope.get('board'))
        if scope.get('board') and board_hash != digest(_retained_path(candidate, scope['board'])):
            raise QualificationError('review board differs from its sealed stage candidate')
        scaffold_row = next((row for row in report['completed'] if row['stage'] == 'scaffold'), None)
        scope_path = (_retained_path(root, scaffold_row['directory']) / 'product/scope.json'
                      if scaffold_row else directory / 'scope.json')
        binding = dict(board_sha256=board_hash, scope_sha256=digest(scope_path), stage=stage['stage'])
        return directory, result, scope, binding, result_sha256
    return None


def checks_dimension(root, review):
    if review is None:
        return _dimension()
    directory, result, scope, binding = review[:4]
    rows = result['checks']
    status = 'pass' if rows else 'not_assessed'
    if any(row['execution'] != 'ok' or row['outcome'] not in ('pass', 'info') for row in rows):
        status = 'failed'
    return dict(_dimension(status, binding=binding, evidence=[_evidence(root, directory / 'review/result.json')]),
                executed=[{key: row[key] for key in ('id', 'kind', 'execution', 'outcome')} for row in rows])


def _review_dimension(root, review):
    if review is None:
        return _dimension()
    directory, result, scope, binding = review[:4]
    if (len(result.get('reviews', [])) < 2 or len(result.get('review_executions', [])) < 2 or not result.get('adjudicated')
            or not all(row['execution'] == 'ok' for row in result.get('review_executions', []))):
        return _dimension(reason='No complete full engineering review is referenced')
    state = result['outcome']
    status = 'pass' if state == 'criteria_satisfied' else 'failed' if state == 'defects_or_unresolved' else 'not_assessed'
    return _dimension(status, binding=binding, evidence=[_evidence(root, directory / 'review/result.json')])


def rules_register(root, review):
    """Expose the existing sourced/default classifications without changing their grade."""
    from .design_rule_provenance import advisory_numeric, require_evaluated_limits
    if review is None:
        return []
    directory, result, scope, binding = review[:4]
    records = []
    for row in result['checks']:
        if row['kind'] not in ('erc', 'drc'):
            continue
        details = row['details']
        item = dict(check=row['id'], kind=row['kind'], status='pass' if row['execution'] == 'ok' else 'failed',
            **{key: copy.deepcopy(details.get(key)) for key in (
                'ignored_checks', 'by_severity', 'constraint_classes', 'advisory_findings_count',
                'waived', 'waived_by_severity', 'graded_unwaived')}, advisories=[], evaluated_sources={})
        if row['kind'] == 'drc':
            constraints = [name for name in row['evidence'] if name.endswith('/drc-constraints.json')]
            native = [name for name in row['evidence'] if name.endswith('/drc.json')]
            if len(constraints) != 1 or len(native) != 1:
                item.update(status='pending', reason='Sourced-rule evaluation evidence is unavailable')
            else:
                constraint_path = _retained_path(directory / 'review', constraints[0])
                document = read_json(constraint_path)
                item['evaluated_sources'] = document['sources']
                item['advisories'] = [v for v in document['violations'] if advisory_numeric(v)]
                item['evidence'] = [_evidence(root, constraint_path)]
                try:
                    require_evaluated_limits(read_json(_retained_path(directory / 'review', native[0])), document['sources'])
                except ValueError as exc:
                    item.update(status='pending', reason=str(exc))
        records.append(item)
    return records


def decision_record(root, plan, candidate, review, judgments):
    """Retain the current captured envelope alongside superseded diagnostic bytes."""
    if plan is None or not _retained_path(candidate, plan['envelope']).exists():
        return dict(status='pending', reason='No captured envelope decision')
    facts = applicability_facts(candidate, plan)
    record = dict(status='pending', **facts, nominal_thickness_mm=facts['board_thickness_mm'],
        thickness_tolerance_source=None,
        height_scope=dict(max_component_height_mm=facts['max_component_height_mm'],
                          source=plan['envelope'], scope='captured component-height limit; supplier/physical fit pending'),
        judgments=[row['sha256'] for row in judgments if 'sha256' in row], supersedes=[])
    previous = _retained_path(candidate, 'manufacturing-decision.json')
    if previous.exists():
        record['supersedes'].append(dict(path='manufacturing-decision.json', sha256=digest(previous),
            reason='Current captured envelope and saved stackup evidence supersede this diagnostic decision'))
    rows = review[1]['checks'] if review else []
    stacks = [row for row in rows if row['kind'] == 'board_envelope'
              and row['details'].get('criterion') == 'stackup/1']
    if len(stacks) != 1:
        record['reason'] = 'Exactly one bound native stackup assessment is required'
        return record
    row = stacks[0]
    details = row['details']
    record['thickness_tolerance_source'] = details.get('finished_thickness_tolerance')
    try:
        same = Decimal(str(details['saved']['thickness_mm'])) == Decimal(str(facts['board_thickness_mm']))
    except (KeyError, InvalidOperation):
        same = False
    record['status'] = ('pass' if same and row['execution'] == 'ok' and row['outcome'] == 'pass'
        and details.get('envelope', {}).get('sha256') == facts['envelope_sha256']
        and details.get('board_sha256') == review[3]['board_sha256'] else 'failed')
    if record['status'] != 'pass':
        record['reason'] = 'Captured nominal thickness or envelope differs from native stackup evidence'
    return record


def _author(value):
    if not isinstance(value, dict) or set(value) != {'agent', 'model', 'date_utc'}:
        raise QualificationError('author/verifier needs agent, model and date_utc metadata')
    if any(not isinstance(value[key], str) or not value[key].strip() or value[key] != value[key].strip() for key in value):
        raise QualificationError('author/verifier metadata must be nonempty text')
    try:
        stamp = datetime.fromisoformat(value['date_utc'].replace('Z', '+00:00'))
    except ValueError as exc:
        raise QualificationError('author/verifier date_utc is invalid') from exc
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
        raise QualificationError('author/verifier date_utc must use UTC')


def admit_judgment(document, *, requirement, span, files, source_text, applicability, binding,
                   judgment_sha256, witness=None):
    """Admit bounded authored testimony; matching metadata alone never evaluates a circuit."""
    from .source_authority import verified_reference
    try:
        _keys(document, ('schema', 'requirement', 'span', 'conclusion', 'assumptions', 'sources',
                         'applicability', 'inputs', 'candidate', 'author', 'unresolved_defects'))
        if document['schema'] != 'agent-ee.engineering-judgment/1':
            raise QualificationError('unsupported judgment schema')
        if document['requirement'] != requirement or document['span'] != span:
            raise QualificationError('judgment requirement/source span is stale')
        _author(document['author'])
        if not isinstance(document['conclusion'], str) or not document['conclusion'].strip():
            raise QualificationError('judgment needs an authored conclusion')
        for key in ('assumptions', 'unresolved_defects'):
            if not isinstance(document[key], list) or any(not isinstance(v, str) or not v.strip() for v in document[key]):
                raise QualificationError('judgment ' + key + ' must be a list of nonempty text')
        if not isinstance(document['inputs'], list) or not isinstance(document['applicability'], dict):
            raise QualificationError('judgment inputs/applicability have invalid shapes')
        if document['candidate'] is None:
            if not document['inputs'] or not document['applicability']:
                raise QualificationError('candidate-independent judgment needs inputs and applicability')
        else:
            expected_candidate = {key: binding.get(key) for key in ('board_sha256', 'scope_sha256')}
            if (any(not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None
                    for value in expected_candidate.values()) or document['candidate'] != expected_candidate):
                raise QualificationError('judgment candidate binding is stale')
        for entry in document['inputs']:
            _keys(entry, ('path', 'sha256'))
            _relative(entry['path'], 'judgment input')
            if files.get(entry['path']) != entry['sha256']:
                raise QualificationError('judgment input hash is stale: ' + entry['path'])
        for key, expected in document['applicability'].items():
            actual = applicability.get(key)
            if actual is None or type(expected) is bool or expected != actual:
                raise QualificationError('judgment applicability is unsupported or mismatched: ' + key)
        if not isinstance(document['sources'], list) or not document['sources']:
            raise QualificationError('judgment needs captured source citations')
        for source in document['sources']:
            _keys(source, ('kind', 'citation'))
            if (source['kind'] not in ('datasheet', 'manufacturer', 'owner', 'engineering')
                    or verified_reference(source['citation'], source_text)['enforcement'] != 'verified'):
                raise QualificationError('judgment source citation is unresolved')
        if document['unresolved_defects']:
            return dict(status='failed', witness='missing', reason='Unresolved engineering defects')
        if witness is None:
            return dict(status='pending', record_status='recorded', witness='missing', reason='Independent witness is missing')
        _keys(witness, ('schema', 'judgment_sha256', 'verifier', 'checked', 'result', 'defects'))
        if witness['schema'] != 'agent-ee.judgment-verification/1':
            raise QualificationError('unsupported judgment witness schema')
        if witness['judgment_sha256'] != judgment_sha256:
            raise QualificationError('judgment witness hash is stale')
        _author(witness['verifier'])
        if witness['verifier']['agent'].casefold() == document['author']['agent'].casefold():
            raise QualificationError('judgment witness is not independent of its author')
        if (not isinstance(witness['checked'], list) or any(not isinstance(v, str) for v in witness['checked'])
                or len(witness['checked']) != len(WITNESS_CHECKS) or set(witness['checked']) != set(WITNESS_CHECKS)):
            raise QualificationError('judgment witness does not attest every required verification')
        if witness['result'] not in ('concur', 'dissent') or not isinstance(witness['defects'], list):
            raise QualificationError('judgment witness has invalid result/defects')
        if witness['result'] == 'dissent' or witness['defects']:
            return dict(status='failed', witness='dissent', reason='Independent witness records dissent or defects')
        return dict(status='recorded', witness='concur')
    except ValueError as exc:
        return dict(status='failed', witness='missing', reason=str(exc))


def _judgments(root, stage, entries, binding, plan):
    directory = stage / 'judgments'
    inventory = read_json(stage / 'judgment-inventory.json')
    if _inventory(directory) != inventory:
        raise QualificationError('frozen judgment inventory differs from package')
    captured = read_json(stage / 'applicability-inventory.json')
    candidate = _retained_path(root, entries['candidate'])
    if _inventory(candidate) != captured['inputs'] or applicability_facts(candidate, plan) != captured['values']:
        raise QualificationError('frozen applicability inputs differ from final package candidate')
    source_text = {}
    witnesses = {}
    for path, pin in inventory.items():
        if path.startswith('verification/') and path.endswith('.json'):
            witness = strict_json(_retained_path(directory, path).read_bytes())
            if not isinstance(witness, dict) or witness.get('schema') != 'agent-ee.judgment-verification/1':
                raise QualificationError('unknown frozen witness schema: ' + path)
            witnesses.setdefault(witness.get('judgment_sha256'), []).append((path, witness))
    output = []
    for entry in entries['requirements']:
        if entry['mode'] != 'judgment':
            continue
        rid = entry.get('parent_id', entry['id'])
        locator = entry['locator']
        span = dict(byte_start=locator['byte_start'], byte_end=locator['byte_end'],
                    text_sha256=entry['obligation_text_sha256'])
        relative = entry['mapping']['record']
        path = _retained_path(directory, relative)
        row = dict(id=rid, span_id=entry['id'], span=span, path=relative)
        if relative not in inventory:
            output.append(dict(row, status='pending', witness='missing', reason='Judgment record is missing'))
            continue
        pin = digest(path)
        document = strict_json(path.read_bytes())
        matched = witnesses.get(pin, [])
        if len(matched) > 1:
            output.append(dict(row, sha256=pin, status='failed', witness='dissent', reason='Ambiguous duplicate witnesses'))
            continue
        sources = document.get('sources', []) if isinstance(document, dict) else []
        for source in sources if isinstance(sources, list) else []:
            citation = source.get('citation', {}) if isinstance(source, dict) else {}
            name = citation.get('path') if isinstance(citation, dict) else None
            if name in captured['inputs']:
                try:
                    source_text[name] = _retained_path(candidate, name).read_text(encoding='utf-8')
                except UnicodeError:
                    continue
        admitted = admit_judgment(document, requirement=rid, span=span, files=captured['inputs'],
            source_text=source_text, applicability=captured['values'], binding=binding,
            judgment_sha256=pin, witness=matched[0][1] if matched else None)
        output.append(dict(row, sha256=pin, **admitted, record=copy.deepcopy(document),
                           verification=copy.deepcopy(matched[0][1]) if matched else None,
                           evidence=[_evidence(root, path), *([_evidence(root, directory / matched[0][0])] if matched else [])]))
    return output


def _clause_status(spans):
    values = [span['status'] for span in spans if span['status'] != 'not_applicable']
    for state in ('failed', 'unknown', 'pending'):
        if state in values:
            return state
    if not values:
        return 'not_applicable'
    return 'recorded' if all(value == 'recorded' for value in values) else 'pass'


def _receipt(root, report, typed):
    scaffold_row = next((row for row in report['completed'] if row['stage'] == 'scaffold'), None)
    if scaffold_row:
        return read_json(_retained_path(root, scaffold_row['directory']) / 'requirements.json')
    requirements = next((row for row in report['completed'] if row['stage'] == 'requirements'), None)
    rows = read_json(_retained_path(root, requirements['directory']) / 'accounting.json')['requirements'] if requirements else []
    return dict(schema='agent-ee.requirement-map-receipt/' + ('2' if typed else '1'), requirements=rows,
                **({'method_binding': 'typed'} if typed else {}))


def _bind_receipt(root, receipt, plan, *, provisional, accounting):
    expected = {}
    for name, field in (('map', 'map_sha256'), ('record', 'spec_record_sha256'),
                        ('spec', 'spec_sha256'), ('obligations', 'obligations_sha256')):
        if name in plan['requirements']:
            expected[field] = digest(_retained_path(root / 'source', plan['requirements'][name]))
    spec = _retained_path(root / 'source', plan['requirements']['spec']).read_bytes()
    try:
        requirement_map.bind_receipt(receipt, spec, source_hashes=expected,
                                     accounting=accounting, provisional=provisional)
    except ValueError as exc:
        raise QualificationError(str(exc)) from exc


def _package_records(rows):
    records = {}
    for name, kind in (('roundtrip', 'fab_roundtrip'), ('orientation', 'cpl_rotation')):
        found = [row for row in rows if row['kind'] == kind]
        records[name] = ('pending' if not found else 'pass' if len(found) == 1
            and found[0]['execution'] == 'ok' and found[0]['outcome'] == 'pass' else 'failed')
    packages = [row for row in rows if row['kind'] == 'fab_package']
    orientation = packages[0].get('details', {}).get('assembly_orientation') if len(packages) == 1 else None
    records['orientation_record'] = copy.deepcopy(orientation)
    if records['orientation'] == 'pass' and (not isinstance(orientation, dict)
            or orientation.get('software_assessment') != 'complete' or orientation.get('unassessed_rows')
            or not orientation.get('profile_sha256')):
        records['orientation'] = 'pending'
    return records


def _methods(root, review):
    if review is None:
        return []
    return [dict(id=row['id'], kind=row['kind'], **{'class': requirement_map.method_class(row['kind'])},
                 evidence=_evidence(root, review[0] / 'review/checks' / (row['id'] + '.json')))
            for row in review[1]['checks']]


def _assess(out):
    root = Path(out).resolve()
    try:
        report = _snapshot(root)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise QualificationError(str(exc), integrity=True) from exc
    plan = _plan(root, report)
    typed = plan is not None and plan['schema'] == TYPED_SCHEMA
    receipt = _receipt(root, report, typed)
    if (receipt['schema'] == 'agent-ee.requirement-map-receipt/2') != typed:
        raise QualificationError('Requirement receipt method binding differs from the captured build plan')
    if typed:
        first = next((row for row in report['completed'] if row['stage'] == 'requirements'), None)
        accounting = read_json(_retained_path(root, first['directory']) / 'accounting.json')['requirements'] if first else []
        _bind_receipt(root, receipt, plan, provisional=not any(row['stage'] == 'scaffold' for row in report['completed']),
                      accounting=accounting)
    review = _checked_review(root, report)
    rows = review[1]['checks'] if review else []
    package = next((row for row in report['completed'] if row['stage'] == 'package'), None)
    dimensions = {name: _dimension() for name in DIMENSIONS}
    for name in ('supplier', 'physical'):
        dimensions[name] = _dimension('pending', reason='No supplier or physical validation performed')
    dimensions['checks'] = checks_dimension(root, review)
    dimensions['engineering_review'] = _review_dimension(root, review)
    coverage = requirement_map.coverage_view(receipt, rows)
    methods = _methods(root, review)
    rules = rules_register(root, review)
    judgments, decision, blockers = [], {}, []
    demonstrated_failure = False
    if typed:
        dimensions['operational'] = _operational(report)
        if 'judgments' in plan:
            for stage in report['completed']:
                if stage['stage'] == 'package':
                    continue
                directory = _retained_path(root, stage['directory'])
                prefix = plan['judgments'] + '/'
                if any(name.startswith(prefix) or '/' + prefix in name for name in _inventory(directory)):
                    raise QualificationError('pre-package stage contains editable judgments')
    if package is not None:
        directory = _retained_path(root, package['directory'])
        candidate = _retained_path(root, package['candidate'])
        marker_path = directory / 'accepted-package.json'
        marker = read_json(marker_path)
        if ('qualification' in marker or marker.get('physical_validation_performed') is not False
                or marker.get('supplier_preview_performed') is not False):
            blockers.append('Package contains unsupported claims')
            demonstrated_failure = True
        elif review is None or review[0] != directory:
            blockers.append('Package has no matching verified checks-only file gate')
        else:
            try:
                gate_plan = plan
                if gate_plan is None:
                    memberships = {row['params']['manifest'] for row in review[2]['checks'] if row['kind'] == 'parts_subset'}
                    if len(memberships) != 1 or 'envelope' not in review[2]:
                        raise QualificationError('Legacy package has no unambiguous manufacturing envelope/parts binding')
                    gate_plan = dict(envelope=review[2]['envelope']['path'],
                        parts=dict(manifest=memberships.pop()), placement={})
                manufacturing_coverage(gate_plan, review[2], receipt['requirements'])
                artifacts = package_artifacts(review[1], review[2], candidate)
                native = {name: pin for name, pin in _inventory(candidate).items()
                          if name.endswith(('.kicad_sch', '.kicad_pcb', '.kicad_pro', '.kicad_dru'))}
                if marker['artifacts'] != artifacts or marker['native_files'] != native:
                    raise QualificationError('accepted package differs from the final gate evidence')
            except ValueError as exc:
                blockers.append(str(exc))
                demonstrated_failure = (isinstance(exc, QualificationError)
                    or any(row['execution'] != 'ok' or row['outcome'] not in ('pass', 'info') for row in rows))
        if typed:
            if marker.get('judgment_inventory_sha256') != digest(directory / 'judgment-inventory.json'):
                raise QualificationError('package judgment inventory binding differs')
            judgments = _judgments(root, directory, dict(receipt, candidate=package['candidate']),
                                   review[3] if review else {}, plan)
            state = 'failed' if any(row['status'] == 'failed' for row in judgments) else (
                'pending' if any(row['status'] != 'recorded' or row['witness'] != 'concur' for row in judgments) else 'pass')
            dimensions['judgments'] = _dimension(state, evidence=[_evidence(root, directory / 'judgment-inventory.json')])
            if state != 'pass':
                blockers.append('Required engineering judgments or independent concurrence remain unresolved')
                demonstrated_failure |= state == 'failed'
            decision = decision_record(root, plan, candidate, review, judgments)
            if decision['status'] != 'pass':
                blockers.append('Current decision is not established by matching native stackup evidence')
                demonstrated_failure |= decision['status'] == 'failed'
            if any(row['status'] != 'pass' for row in rules):
                blockers.append('Applicable sourced-rule evaluation is incomplete')
                demonstrated_failure |= any(row['status'] == 'failed' for row in rules)
    judgments_by_span = {row['span_id']: row for row in judgments}
    entries = {row['id']: row for row in receipt['requirements']}
    if typed:
        for clause in coverage.values():
            for span in clause['spans']:
                if span['mode'] == 'operational':
                    criterion = entries[span['id']]['mapping']['criterion']
                    span['status'] = (dimensions['operational']['status'] if criterion == 'public_build_and_resume'
                                      else 'pending')
                    if span['status'] == 'not_assessed':
                        span['status'] = 'pending'
                    if criterion != 'public_build_and_resume':
                        span['reason'] = 'Unsupported operational criterion'
                elif span['mode'] == 'judgment' and span['id'] in judgments_by_span:
                    judgment = judgments_by_span[span['id']]
                    span.update(status=judgment['status'], witness=judgment['witness'])
            clause['status'] = _clause_status(clause['spans'])
        if any(span['status'] not in ('pass', 'recorded', 'not_applicable')
               for clause in coverage.values() for span in clause['spans']
               if span['obligation'] in ('executable', 'judgment')):
            blockers.append('Required software-file or judgment source spans are not satisfied')
            demonstrated_failure |= any(span['status'] == 'failed' for clause in coverage.values()
                for span in clause['spans'] if span['obligation'] in ('executable', 'judgment'))
        if any(span['status'] == 'failed' for clause in coverage.values() for span in clause['spans']
               if span['obligation'] == 'operational'):
            blockers.append('Required operational assessment failed')
            demonstrated_failure = True
    package_records = _package_records(rows)
    bound = {cid for clause in coverage.values() for span in clause['spans'] for cid in span['bound_ids']}
    if typed and any(package_records[name] != 'pass' and any(row['kind'] == kind and row['id'] in bound for row in rows)
                     for name, kind in (('roundtrip', 'fab_roundtrip'), ('orientation', 'cpl_rotation'))):
        blockers.append('Required exact-package evidence is incomplete')
        demonstrated_failure |= any(package_records[name] == 'failed'
            and any(row['kind'] == kind and row['id'] in bound for row in rows)
            for name, kind in (('roundtrip', 'fab_roundtrip'), ('orientation', 'cpl_rotation')))
    if package is not None:
        dimensions['file_release'] = _dimension('failed' if demonstrated_failure else 'pending' if blockers else 'pass',
            binding=review[3] if review else None, evidence=[_evidence(root, marker_path)],
            reason='; '.join(blockers) if blockers else None)
    elif report['status'] == 'failed' and report.get('attempts') and report['attempts'][-1]['stage'] == 'package':
        dimensions['file_release'] = _dimension('failed', reason=report.get('reason'))
    record = _record('typed' if typed else 'legacy', dimensions, claims_withheld=not typed,
        coverage=coverage, methods=methods, judgments=judgments, rules=rules, decision=decision,
        package_records=package_records, summary=dict(pending_required=sorted(
            rid for rid, clause in coverage.items() if clause['status'] not in ('pass', 'recorded', 'not_applicable')),
            file_release_blockers=blockers))
    if _snapshot(root) != report:
        raise QualificationError('Build evidence changed during qualification assessment', integrity=True)
    if review is not None:
        from .cli import verify
        if verify(review[0] / 'review') != review[1] or digest(review[0] / 'review/result.json') != review[4]:
            raise QualificationError('Ordinary review evidence changed during assessment', integrity=True)
    return _publish(root, record)


def assess(out):
    """Regenerate a root record from verified completed stages, never from old qualification."""
    try:
        return _assess(out)
    except QualificationError:
        raise
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise QualificationError(str(exc)) from exc


def failed_assessment(out, report, reason, *, typed, integrity=False):
    """Publish only failure claims when custody or assessment cannot be verified."""
    dimensions = {name: _dimension(reason='Evidence assessment did not complete') for name in DIMENSIONS}
    dimensions['file_release'] = _dimension('failed', reason=reason)
    if integrity:
        dimensions['operational'] = _dimension('failed', reason=reason)
    for name in ('supplier', 'physical'):
        dimensions[name] = _dimension('pending')
    return _publish(Path(out), _record('typed' if typed else 'legacy', dimensions,
        summary=dict(pending_required=[], assessment_error=reason)))


def assess_review(out, result, selection):
    """A diagnostic checks-only review cannot grant a build or package claim."""
    root = Path(out)
    manifest = read_json(root / 'review/candidate.json')
    binding = dict(board_sha256=manifest['files'].get(manifest['scope'].get('board')),
        scope_sha256=selection['scope_sha256'], stage=selection['selected']['stage'])
    dimensions = {name: _dimension() for name in DIMENSIONS}
    review = (root, result, manifest['scope'], binding)
    dimensions['checks'] = checks_dimension(root, review)
    return _publish(root, _record(selection.get('qualification_mode', 'legacy'), dimensions,
        methods=_methods(root, review), rules=rules_register(root, review)))
