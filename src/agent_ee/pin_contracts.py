"""Authored, page-cited physical-pin obligations over retained native connectivity."""

from .check_registry import register as _register_check
import argparse
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .candidate import safe_relative
from .catalog import exact_fields, nonempty, strict_json
from .datasheet_facts import load_facts, load_obligations
from .parts_manifest import dependency_closure, load_manifest, read_captured
from .storage import digest, read_json, write_json

SCHEMA = 'agent-ee.pin-contract/1'
RESULT_SCHEMA = 'agent-ee.pin-contracts-result/1'
VOLTAGE = frozenset(('requires_voltage', 'provides_voltage'))
PRESENCE = frozenset(('must_be_connected', 'do_not_connect',
    'should_have_decoupling_capacitor', 'needs_external_pullup', 'capacitor_to_pin', 'feedback_divider'))
KINDS = VOLTAGE | PRESENCE
LIMIT = ('Authored applicability and direct native connectivity only. No voltage inference through '
         'components, automatic datasheet mining, capacitor sizing/placement, pull-up sizing, '
         'divider ratio, catalog qualification or authenticated owner approval.')


def validate_contract(contract):
    exact_fields(contract, ('schema', 'part_number', 'manufacturer', 'pins'))
    if contract['schema'] != SCHEMA:
        raise ValueError('unsupported pin contract schema')
    for key in ('part_number', 'manufacturer'):
        nonempty(contract[key])
    if not isinstance(contract['pins'], list) or len(contract['pins']) > 4096:
        raise ValueError('bounded pin array required')
    for pin in contract['pins']:
        exact_fields(pin, ('pin', 'obligations'))
        nonempty(pin['pin'])
        if not isinstance(pin['obligations'], list) or not pin['obligations']:
            raise ValueError('pin requires obligations')
        kinds = set()
        for obligation in pin['obligations']:
            kind = obligation.get('kind') if isinstance(obligation, dict) else None
            if kind not in KINDS or kind in kinds:
                raise ValueError('unsupported or duplicate pin obligation')
            kinds.add(kind)
            exact_fields(obligation, ('kind', 'facts', 'other_pin') if kind == 'capacitor_to_pin'
                         else ('kind', 'facts'))
            facts = obligation['facts']
            if (not isinstance(facts, list) or len(facts) not in ((1, 2) if kind in VOLTAGE else (1,))
                    or any(not isinstance(fid, str) or not fid.strip() for fid in facts)
                    or len(facts) != len(set(facts))):
                raise ValueError('obligation requires distinct fact IDs (scalar or min/max for voltage)')
            if kind == 'capacitor_to_pin':
                nonempty(obligation['other_pin'])
                if obligation['other_pin'] == pin['pin']:
                    raise ValueError('capacitor endpoints must differ')
    return contract


def interval(value):
    """Exact declared volts: a scalar or a closed [min, max], never prose or bool."""
    ends = value if isinstance(value, list) else [value, value]
    if len(ends) != 2 or any(type(v) not in (int, float, str) for v in ends):
        raise ValueError('voltage requires scalar or [min, max]')
    if any(not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', str(v)) for v in ends):
        raise ValueError('voltage is not plain numeric volts')
    try:
        low, high = map(lambda v: Decimal(str(v)), ends)
    except InvalidOperation as exc:
        raise ValueError('invalid voltage') from exc
    if not low.is_finite() or not high.is_finite() or low > high:
        raise ValueError('invalid/reversed voltage interval')
    return low, high


def load_inputs(params, scope, files, inputs, check_id):
    exact_fields(params, ('manifest', 'facts_check', 'netlist_check', 'ground_nets', 'power_nets', 'rails', 'classes'))
    specs = scope['checks']
    prior = specs[:next(i for i, s in enumerate(specs) if s['id'] == check_id)]
    facts_spec = next((s for s in prior if s['id'] == params['facts_check'] and s['kind'] == 'datasheet_facts'), None)
    if not facts_spec or not any(s['id'] == params['netlist_check'] and s['kind'] == 'netlist' for s in prior):
        raise ValueError('pin contracts require earlier facts and native netlist checks')
    if inputs is None:
        raise ValueError('pin contracts require captured inputs')
    document = load_manifest(inputs, params['manifest'])
    dependencies = set(dependency_closure(document, inputs)) | {str(safe_relative(params['manifest']))}
    contracts = {}
    for part in document['parts']:
        if 'pin_contract' in part:
            rel = part['pin_contract']['path']
            contracts[part['id']] = validate_contract(strict_json(read_captured(inputs, rel)))
    for key in ('ground_nets', 'power_nets'):
        values = params[key]
        if (not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values)
                or len(values) != len(set(values))):
            raise ValueError('explicit unique net names required')
    if set(params['ground_nets']) & set(params['power_nets']):
        raise ValueError('power and ground classes overlap')
    if not isinstance(params['rails'], dict):
        raise ValueError('rails must map exact net names to volts')
    for name, value in params['rails'].items():
        nonempty(name)
        interval(value)
    if not set(params['rails']) <= set(params['power_nets']):
        raise ValueError('declared voltage rails must be power-class nets')
    if not isinstance(params['classes'], dict):
        raise ValueError('classes must map exact symbols to two-terminal classes')
    for lib_id, rule in params['classes'].items():
        if nonempty(lib_id).count(':') != 1:
            raise ValueError('class requires exact library:symbol')
        exact_fields(rule, ('class', 'pins'))
        if (rule['class'] not in ('capacitor', 'resistor', 'other') or not isinstance(rule['pins'], list)
                or not rule['pins'] or any(not isinstance(p, str) for p in rule['pins'])
                or len(set(rule['pins'])) != len(rule['pins'])
                or (rule['class'] != 'other' and len(rule['pins']) != 2)):
            raise ValueError('class requires distinct physical pins; R/C require two')
        for pin in rule['pins']:
            nonempty(pin)
    rel = facts_spec['params']['facts']
    dependencies.add(str(safe_relative(rel)))
    facts = load_facts(Path(inputs), rel)
    dependencies.update(f['source']['path'] for f in facts)
    envelope = load_obligations(Path(inputs), rel)
    if envelope:
        dependencies.update(envelope['dependencies'])
    if not dependencies <= files.keys():
        raise ValueError('uncaptured pin-contract dependency: ' + repr(sorted(dependencies - files.keys())))
    for path in dependencies:
        if files[path] != digest(Path(inputs) / path):
            raise ValueError('pin-contract dependency changed: ' + path)
    return document, contracts, facts, dependencies


def capability(params, scope, files, inputs, check_id):
    document, contracts, _, dependencies = load_inputs(params, scope, files, inputs, check_id)
    predicates = set()
    for part in document['parts']:
        contract = contracts.get(part['id'])
        if contract:
            for pin in contract['pins']:
                for obligation in pin['obligations']:
                    if obligation['kind'] != 'provides_voltage':
                        predicates.add('pin-contracts:' + obligation['kind'])
    result = dict(immutable_inputs=sorted(dependencies), predicates=sorted(predicates))
    source = next(s for s in scope['checks'] if s['id'] == params['facts_check'])
    envelope = load_obligations(Path(inputs), source['params']['facts'])
    if envelope is not None:
        result['acquisitions'] = envelope['acquired']
    return result


def _fact_support(part, contract, pin, obligation, facts, verified):
    sources, selected = [], []
    for fid in obligation['facts']:
        fact = facts.get(fid)
        if fact:
            sources.append(dict(fact_id=fid, **fact['source']))
        selected.append(fact)
    reason = None
    if (contract['part_number'] != part['mpn'] or contract['manufacturer'] != part['manufacturer']):
        reason = 'manufacturer or part-number mismatch'
    elif part['datasheet']['status'] != 'captured':
        reason = 'part datasheet unavailable'
    else:
        pdf = part['datasheet']['artifact']
        for fid, fact in zip(obligation['facts'], selected):
            if (fact is None or verified.get(fid) != fact or fact['part_number'] != part['mpn']
                    or fact.get('pin') != pin or fact['kind'] != obligation['kind']
                    or any(fact['source'][k] != pdf[k] for k in ('path', 'sha256'))):
                reason = 'fact absent, unverified or inapplicable to part/PDF/physical pin/kind'
                break
    voltage = None
    if reason is None:
        if obligation['kind'] in VOLTAGE:
            try:
                if any(f['unit'] != 'V' for f in selected):
                    raise ValueError('voltage facts require V units')
                voltage = interval([selected[0]['value'], selected[-1]['value']])
            except ValueError as exc:
                reason = str(exc)
        elif selected[0]['unit'] != '':
            reason = 'presence citation requires empty units; the contract authors the obligation'
    return sources, reason, voltage


def _bindings(document, contracts, graph, facts, verified):
    rows, coverage = [], []
    declared = {(s['sheet'], s['reference']): p for p in document['parts'] for s in p['instances']}
    observed = {(c['sheet'], ref): c for ref, c in graph['components'].items()}
    for key in sorted(declared.keys() | observed.keys()):
        part, comp = declared.get(key), observed.get(key)
        ref = key[1]
        if part is not None and comp is None and not part['required']:
            coverage.append(dict(reference=ref, sheet=key[0], status='excluded', reason='optional absent instance'))
            continue
        contract = contracts.get(part['id']) if part else None
        identity_reason = None
        if part is None or comp is None:
            identity_reason = 'unmatched part/native instance'
        elif not comp['populated']:
            identity_reason = 'unpopulated native instance; electrical behavior unsupported'
        elif part.get('kind') == 'copper_feature':
            identity_reason = 'copper feature is not purchased-part electrical evidence'
        elif part.get('kind') == 'bare_copper_probe':
            identity_reason = 'bare copper probe is not purchased-part electrical evidence'
        elif (comp['lib_id'] != part['lib_id'] or any(comp['properties'].get(document['property_fields'][k]) != part[k]
                 for k in ('mpn', 'manufacturer'))):
            identity_reason = 'native symbol, manufacturer or part-number mismatch'
        if contract is None or not contract['pins']:
            coverage.append(dict(reference=ref, sheet=key[0], status='unsupported',
                                 reason=identity_reason or ('no contract file' if contract is None else 'empty contract'),
                                 pins=list(comp['pins']) if comp else []))
            continue
        counts = Counter(p['pin'] for p in contract['pins'])
        covered = set()
        for pin in contract['pins']:
            number = pin['pin']
            covered.add(number)
            reason = identity_reason
            if counts[number] != 1:
                reason = 'ambiguous physical pin contract'
            elif comp and number not in comp['pins']:
                reason = 'unmatched physical pin'
            for obligation in pin['obligations']:
                sources, fact_reason, voltage = _fact_support(part, contract, number, obligation, facts, verified)
                net = graph['memberships'].get((ref, number)) if comp else None
                row = dict(reference=ref, sheet=key[0], pin=number, kind=obligation['kind'],
                           status='unknown', sources=sources, net_id=net,
                           locator=f'component {ref}, sheet {key[0]}, physical pin {number}: {obligation["kind"]}')
                row['reason'] = reason or fact_reason
                row['bound'] = row['reason'] is None
                if voltage:
                    row['voltage_v'] = [str(v) for v in voltage]
                if 'other_pin' in obligation:
                    row['other_pin'] = obligation['other_pin']
                if row['reason'] is None and net is None:
                    row['reason'] = 'physical pin has no exported native membership'
                rows.append(row)
        if comp:
            uncovered = sorted(set(comp['pins']) - covered)
            if uncovered:
                coverage.append(dict(reference=ref, sheet=key[0], status='unsupported',
                                     reason='pins without contract', pins=uncovered))
    return rows, coverage


def _two_terminal(graph, classes):
    edges, unresolved = [], []
    for ref, comp in graph['components'].items():
        if not comp['populated']:
            continue
        rule = classes.get(comp['lib_id'])
        if rule is None:
            # An undeclared symbol may be an unclassified capacitor/resistor.
            unresolved.append(ref)
            continue
        pins = rule['pins']
        nets = [graph['memberships'].get((ref, p)) for p in pins]
        if set(pins) != set(comp['pins']) or None in nets:
            unresolved.append(ref)
            continue
        if rule['class'] == 'other':
            continue
        edges.append(dict(reference=ref, kind=rule['class'], nets=nets, pins=pins))
    return edges, unresolved


def _presence(row, graph, params, edges, unresolved):
    net, kind = row['net_id'], row['kind']
    terminals = graph['nets'][net]['terminals']
    if kind in ('must_be_connected', 'do_not_connect'):
        connected = len(terminals) > 1
        row.update(status='pass' if connected == (kind == 'must_be_connected') else 'defect',
                   members=[dict(reference=r, pin=p) for r, p in terminals])
        return
    ground = {nid for nid, n in graph['nets'].items() if n['name'] in params['ground_nets']}
    power = {nid for nid, n in graph['nets'].items() if n['name'] in params['power_nets']}
    target, cls = ground, 'capacitor'
    if kind == 'capacitor_to_pin':
        other = graph['memberships'].get((row['reference'], row['other_pin']))
        if other is None:
            row['reason'] = 'capacitor target pin has no native membership'
            return
        target = {other}
    elif kind == 'needs_external_pullup':
        target, cls = power, 'resistor'
    elif kind == 'feedback_divider':
        cls = 'resistor'
    if not target or (kind == 'feedback_divider' and not power):
        row['reason'] = 'target rail class has no native net'
        return
    def matching(targets):
        return [e for e in edges if e['kind'] == cls and e['nets'][0] != e['nets'][1]
                and net in e['nets'] and any(n in targets for n in e['nets'] if n != net)]
    found = matching(target)
    if kind == 'feedback_divider':
        top = matching(power)
        pairs = [(a, b) for a in found for b in top if a['reference'] != b['reference']]
        found = [edge for pair in pairs for edge in pair]
    row['components'] = sorted({e['reference'] for e in found})
    if found:
        row['status'] = 'pass'
    else:
        # Missing presence is conclusive only when every possible attached element is classified.
        attached = {ref for ref in unresolved if any(r == ref for r, _ in terminals)
                    and ref != row['reference']}
        if attached:
            row['reason'] = 'unclassified/unsupported attached elements: ' + ', '.join(sorted(attached))
        else:
            row['status'] = 'defect'


def _voltage(row, providers, params, graph, unresolved_providers):
    provided = providers.get(row['net_id'], [])
    name = graph['nets'][row['net_id']]['name']
    if name in params['rails']:
        provided = [*provided, dict(reference=None, pin=None,
            voltage_v=[str(v) for v in interval(params['rails'][name])],
            sources=[dict(locator='scope check params.rails[' + repr(name) + ']')])]
    row['providers'] = provided
    if row['net_id'] in unresolved_providers or not provided:
        row['reason'] = 'unverified provider on net' if provided else 'no verified direct-net voltage provider'
        return
    low, high = interval(row['voltage_v'])
    row['status'] = 'pass' if all(low <= interval(p['voltage_v'])[0] <= interval(p['voltage_v'])[1] <= high
                                                 for p in provided) else 'defect'


def evaluate(document, contracts, facts, verified, graph, params):
    """Pure diagnostic evaluation; unknown coverage dominates overall acceptance."""
    facts = {f['id']: f for f in facts}
    rows, coverage = _bindings(document, contracts, graph, facts, verified)
    edges, unresolved = _two_terminal(graph, params['classes'])
    providers, unresolved_providers = {}, set()
    for row in rows:
        if row['kind'] != 'provides_voltage':
            continue
        if row['reason'] is not None:
            unresolved_providers.add(row['net_id'])
        else:
            row['status'] = 'pass'
            providers.setdefault(row['net_id'], []).append({k: row[k] for k in
                ('reference', 'pin', 'voltage_v', 'sources', 'locator')})
    for row in rows:
        if row['reason'] is not None or row['kind'] == 'provides_voltage':
            continue
        if row['kind'] == 'requires_voltage':
            _voltage(row, providers, params, graph, unresolved_providers)
        else:
            _presence(row, graph, params, edges, unresolved)
    unknown = any(r['status'] == 'unknown' for r in rows) or any(c['status'] == 'unsupported' for c in coverage) or not rows
    counts = Counter(r['status'] for r in rows)
    counts.update(unsupported=sum(c['status'] == 'unsupported' for c in coverage),
                  pins_with_contract=len({(r['sheet'], r['reference'], r['pin']) for r in rows}),
                  ambiguous=sum(r['reason'] == 'ambiguous physical pin contract' for r in rows))
    return dict(schema=RESULT_SCHEMA, records=rows, coverage=coverage, counts=dict(counts),
                execution='unsupported' if unknown else 'ok',
                outcome='unknown' if unknown else 'defect' if counts['defect'] else 'pass')


def check_pin_contracts(ctx):
    from .checks import CheckError
    from .datasheet_facts import prerequisite
    from .netlist_extra import native_graph
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    _, xml, _, _ = native_graph(ctx)
    facts_path = prerequisite(ctx, ctx.params['facts_check'], 'datasheet_facts', 'facts.json', allow_unsupported=True)
    ctx.execute(['/usr/bin/python3', '-m', 'agent_ee.pin_contracts',
                 '--manifest', '/evidence/candidate.json', '--id', ctx.cid,
                 '--facts', '/evidence/facts.json', '--netlist', '/evidence/design.xml'], 120,
                extra_ro=[(xml, '/evidence/design.xml'), (facts_path, '/evidence/facts.json'),
                          (ctx.run_dir / 'candidate.json', '/evidence/candidate.json')])
    report = ctx.load_json('pin-contracts.json', 'pin contracts')
    ctx.evidence('pin-contracts.json')
    if (report.get('schema') != RESULT_SCHEMA or report.get('native_xml_sha256') != digest(xml)
            or report.get('facts_sha256') != digest(facts_path)
            or report.get('candidate_id') != ctx.manifest['candidate_id']):
        raise CheckError('malformed', 'pin contract source identity differs')
    ctx.result['details'].update(report)
    ctx.result['details']['evaluated_predicates'] = descriptor['predicates'] if report['execution'] == 'ok' else []
    ctx.result['limitations'].append(LIMIT)
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
                      summary='Datasheet pin contracts: ' + report['outcome'])
    defects = [r for r in report['records'] if r['status'] == 'defect']
    if defects:
        ctx.result['location'] = '; '.join(r['locator'] for r in defects)
        ctx.result['evidence'].remove(f'work/{ctx.cid}/pin-contracts.json')
        ctx.result['evidence'].extend(dict(path=f'work/{ctx.cid}/pin-contracts.json', locator=r['locator']) for r in defects)


def main():
    from .native_netlist import read_native_inventory
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'id', 'facts', 'netlist'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    manifest = read_json(args.manifest)
    scope = manifest['scope']
    spec = next(s for s in scope['checks'] if s['id'] == args.id and s['kind'] == 'pin_contracts')
    params = spec['params']
    root = Path('/candidate')
    document, contracts, facts, _ = load_inputs(params, scope, manifest['files'], root, args.id)
    facts_report = read_json(args.facts)
    retained = facts_report['facts']
    ids = [r['id'] for r in retained]
    if len(ids) != len(set(ids)):
        raise ValueError('ambiguous retained fact IDs')
    verified = {r['id']: r['fact'] for r in retained if r['status'] == 'verified'}
    report = evaluate(document, contracts, facts, verified, read_native_inventory(args.netlist), params)
    report['acquisitions'] = facts_report.get('acquisitions', {})
    report.update(candidate_id=manifest['candidate_id'], native_xml_sha256=digest(args.netlist),
                  facts_sha256=digest(args.facts))
    write_json(Path('/work/pin-contracts.json'), report)


if __name__ == '__main__':
    main()


def describe_pin_contracts(ctx):
    descriptor = capability(ctx.params, ctx.scope, ctx.files, ctx.inputs, ctx.spec['id'])
    ctx.prerequisite(ctx.params.get('netlist_check'), ('netlist',))
    ctx.prerequisite(ctx.params.get('facts_check'), ('datasheet_facts',))
    ctx.apply(descriptor)
    if 'acquisitions' in descriptor:
        ctx.result['acquisitions'] = descriptor['acquisitions']


if __name__ != '__main__':
    _register_check('pin_contracts', describe_pin_contracts, check_pin_contracts)
