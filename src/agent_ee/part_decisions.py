"""Source-bound component choices and recorded substitutions over native identities.

The captured projection is a reviewed declaration, not natural-language part
selection. Its source rows, exclusions and normalization reasons remain visible.
"""
import argparse
import re
from pathlib import Path

from .candidate import safe_relative
from .hardware_interface import exact_keys, text
from .netclass_completeness import load_json
from .part_binding import LCSC_RE
from .spec_record import verify, write_new
from .storage import digest, json_bytes, write_json

SCHEMA = 'agent-ee.part-decisions/1'


def part(value):
    exact_keys(value, 'mpn lcsc')
    if value['mpn'] is not None:
        text(value['mpn'])
    if value['lcsc'] is not None and (not isinstance(value['lcsc'], str) or not LCSC_RE.fullmatch(value['lcsc'])):
        raise ValueError('expected LCSC C followed by a positive integer')
    if value['mpn'] is None and value['lcsc'] is None:
        raise ValueError('part needs an MPN or LCSC identity')


def key(row):
    return row['requirement_id'], row['mpn'], row['lcsc']


def compile_decisions(record_raw, spec_raw, declaration):
    """Validate an explicit projection; attach exact source rows and byte hashes.

    Every row of the selected source section is either projected or explicitly
    excluded. Multiple decisions can share a requirement ID. Exact part strings
    must occur in the Decision cell, unless a visible normalization is supplied.
    """
    record = verify(record_raw, spec_raw)
    exact_keys(declaration, 'section decisions exclusions')
    rows = {r['id']: r for r in record['requirements'] if r['section'] == declaration['section']}
    if not rows or any(r['kind'] != 'table_row' for r in rows.values()):
        raise ValueError('component decisions require a nonempty table section')
    if not isinstance(declaration['decisions'], list) or not declaration['decisions']:
        raise ValueError('at least one component decision required')
    seen, covered = set(), set()
    for decision in declaration['decisions']:
        fields = 'requirement_id function mpn lcsc symbol_hint footprint_hint'
        exact_keys(decision, fields + (' normalization' if 'normalization' in decision else ''))
        rid = decision['requirement_id']
        if rid not in rows:
            raise ValueError('decision has no matching source requirement')
        cells = rows[rid]['cells']
        if decision['function'] != cells.get('Function') or not isinstance(cells.get('Decision'), str):
            raise ValueError('decision function must match the source Function cell')
        part({k: decision[k] for k in ('mpn', 'lcsc')})
        for hint in ('symbol_hint', 'footprint_hint'):
            if decision[hint] is not None:
                text(decision[hint])
        source_mpn = decision['mpn']
        if 'normalization' in decision:
            normalization = decision['normalization']
            exact_keys(normalization, 'source_mpn reason')
            source_mpn = text(normalization['source_mpn'])
            text(normalization['reason'])
            if decision['mpn'] is None:
                raise ValueError('normalization requires an MPN')
        for identity in (source_mpn, decision['lcsc']):
            if identity is not None and not re.search(
                    r'(?<![A-Za-z0-9_/-])' + re.escape(identity) + r'(?![A-Za-z0-9_/-])', cells['Decision']):
                raise ValueError('part identity is absent from source Decision cell')
        if key(decision) in seen:
            raise ValueError('duplicate decision')
        seen.add(key(decision))
        covered.add(rid)
    if not isinstance(declaration['exclusions'], list):
        raise ValueError('exclusions must be explicit array')
    for exclusion in declaration['exclusions']:
        exact_keys(exclusion, 'requirement_id classification reason evidence_refs')
        if exclusion['classification'] not in ('non_part_constraint', 'unresolved_part_decision'):
            raise ValueError('unsupported source-row classification')
        refs = exclusion['evidence_refs']
        if not isinstance(refs, list) or (exclusion['classification'] == 'non_part_constraint' and not refs):
            raise ValueError('non-part classification requires captured evidence references')
        for ref in refs:
            exact_keys(ref, 'path locator')
            safe_relative(ref['path'])
            text(ref['locator'])
        rid = exclusion['requirement_id']
        text(exclusion['reason'])
        if rid not in rows or rid in covered:
            raise ValueError('unknown, duplicate or projected exclusion')
        covered.add(rid)
    if covered != set(rows):
        raise ValueError('source component-decision rows are missing from projection')
    return {'schema': SCHEMA, 'spec': record['spec'], 'declaration': declaration,
            'source_rows': list(rows.values())}


def capability(params, scope, files, inputs, check_id):
    exact_keys(params, 'netlist_check decisions spec_record spec deviations')
    immutable = [params[k] for k in ('decisions', 'spec_record', 'spec')]
    paths = immutable + ([params['deviations']] if params['deviations'] is not None else [])
    if len(paths) != len(set(paths)) or inputs is None:
        raise ValueError('expected distinct captured decision inputs')
    for path in paths:
        if str(safe_relative(path)) != path or path not in files:
            raise ValueError('decision inputs must be captured')
    inputs = Path(inputs)
    policy = load_json(inputs / params['decisions'])
    expected = compile_decisions((inputs / params['spec_record']).read_bytes(),
                                (inputs / params['spec']).read_bytes(), policy['declaration'])
    if policy != expected:
        raise ValueError('decision table differs from its source-bound compilation')
    mapped = {r['id']: r for r in scope.get('baseline', {}).get('requirements', []) if check_id in r['checks']}
    decisions = policy['declaration']['decisions']
    for decision in decisions:
        rid = decision['requirement_id']
        if rid not in mapped or mapped[rid]['source']['path'] != params['spec']:
            raise ValueError('each decision requires a mapped requirement from the captured spec')
    for exclusion in policy['declaration']['exclusions']:
        for ref in evidence_references(exclusion['evidence_refs'], inputs, files):
            if ref['path'] == params['deviations']:
                raise ValueError('classification evidence cannot be mutable deviations')
            if ref['path'] not in immutable:
                immutable.append(ref['path'])
    return {'policy': policy, 'immutable_inputs': immutable,
            'predicates': sorted({'part-decisions:' + d['requirement_id'] for d in decisions})}


def evidence_references(refs, inputs, files):
    evidence = []
    for ref in refs:
        exact_keys(ref, 'path locator')
        rel = str(safe_relative(ref['path']))
        text(ref['locator'])
        if rel != ref['path'] or rel not in files:
            raise ValueError('evidence must name a captured input')
        path = Path(inputs) / rel
        if path.stat().st_size == 0:
            raise ValueError('empty referenced evidence')
        evidence.append({**ref, 'sha256': digest(path)})
    return evidence


def evaluate(policy, deviations, components, inputs, files):
    """Presence only; rationale and references are reported without suitability judgment."""
    decisions = policy['declaration']['decisions']
    exact_keys(deviations, 'schema deviations')
    if deviations['schema'] != 'agent-ee.part-deviations/1' or not isinstance(deviations['deviations'], list):
        raise ValueError('unsupported part deviations')
    by_key, defects, receipts = {}, [], []
    for index, deviation in enumerate(deviations['deviations']):
        receipt = {'index': index, 'declaration': deviation, 'violations': []}
        receipts.append(receipt)
        try:
            fields = 'requirement_id replacement reason evidence_refs'
            exact_keys(deviation, fields + (' original' if 'original' in deviation else ''))
            candidates = [d for d in decisions if d['requirement_id'] == deviation['requirement_id']]
            if 'original' in deviation:
                part(deviation['original'])
                candidates = [d for d in candidates if all(d[k] == deviation['original'][k] for k in ('mpn', 'lcsc'))]
            if len(candidates) != 1:
                raise ValueError('deviation needs exactly one original decision; use original for a multi-part row')
            part(deviation['replacement'])
            text(deviation['reason'])
            refs = deviation['evidence_refs']
            if not isinstance(refs, list) or not refs:
                raise ValueError('deviation requires captured evidence references')
            evidence = evidence_references(refs, inputs, files)
            identity = key(candidates[0])
            if identity in by_key:
                raise ValueError('duplicate deviation for a decision')
            by_key[identity] = deviation
            receipt['evidence'] = evidence
        except (ValueError, TypeError, KeyError, OSError) as exc:
            receipt['violations'].append(str(exc))
            defects.append(f'deviation {index}: {exc}')
    records = []
    for decision in decisions:
        deviation = by_key.get(key(decision))
        selected = deviation['replacement'] if deviation else decision
        matches = []
        for ref, component in sorted(components.items()):
            fields = component['binding_fields']
            if (component['populated'] and component['on_board'] and
                    (selected['mpn'] is None or fields['Partnumber'] == selected['mpn']) and
                    (selected['lcsc'] is None or fields['LCSC'] == selected['lcsc'])):
                matches.append({'reference': ref, 'locator': component['locator'],
                    'symbol': component['lib_id'], 'value': component['value'],
                    'footprint': component['footprint'], 'fields': fields})
        violations = [] if matches else ['replacement_absent' if deviation else 'decision_absent']
        records.append({'decision': decision, 'deviation': deviation, 'matches': matches,
                        'violations': violations, 'status': 'satisfied' if matches else 'violation'})
    exclusions = [{**e, 'evidence': evidence_references(e['evidence_refs'], inputs, files),
                   'status': 'outside_predicate' if e['classification'] == 'non_part_constraint' else 'not_evaluated'}
                  for e in policy['declaration']['exclusions']]
    unresolved = [e for e in exclusions if e['status'] == 'not_evaluated']
    return {'schema': 'agent-ee.part-decisions-result/1', 'records': records,
            'deviations': receipts, 'deviation_defects': defects,
            'exclusions': exclusions, 'unresolved': unresolved,
            'execution': 'unsupported' if unresolved else 'ok',
            'outcome': ('unknown' if unresolved else
                        'defect' if defects or any(r['violations'] for r in records) else 'pass')}


def check_part_decisions(ctx):
    from .checks import CheckError
    from .netlist_extra import native_graph
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    prior, xml, connectivity, graph = native_graph(ctx)
    try:
        deviations = (load_json(ctx.inputs / ctx.params['deviations']) if ctx.params['deviations'] is not None
                      else {'schema': 'agent-ee.part-deviations/1', 'deviations': []})
        report = evaluate(descriptor['policy'], deviations, graph['components'], ctx.inputs, ctx.manifest['files'])
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('malformed', str(exc)) from exc
    report.update(candidate_id=ctx.manifest['candidate_id'], netlist_check=prior['id'],
                  native_xml_sha256=digest(xml), connectivity_sha256=digest(connectivity),
                  decisions_sha256=digest(ctx.inputs / ctx.params['decisions']))
    if ctx.params['deviations'] is not None:
        report['deviations_sha256'] = digest(ctx.inputs / ctx.params['deviations'])
    write_json(ctx.work / 'part_decisions.json', report)
    ctx.evidence('part_decisions.json')
    ctx.result['details'].update(report)
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
                      summary=f"Component decisions: {report['outcome']}; {len(report['records'])} choices checked")
    ctx.result['limitations'].append('Presence of exact native Partnumber/LCSC fields on populated on-board '
        'components only. Manufacturer is reported. Symbol and footprint hints are informational. '
        'Projection completeness within each prose row, normalizations, exclusions, deviation rationale '
        'and cited evidence suitability require review. No electrical, pin-map, procurement or bench approval.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--spec-record', type=Path, required=True)
    parser.add_argument('--declaration', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = compile_decisions(args.spec_record.read_bytes(), args.spec.read_bytes(), load_json(args.declaration))
        write_new(args.out, json_bytes(result))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.exit(1, f'part-decisions refused: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
