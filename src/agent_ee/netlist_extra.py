"""Declared direct-net topology checks using the shared strict native XML reader."""
from collections import defaultdict
from pathlib import Path
from xml.etree.ElementTree import ParseError

from .candidate import safe_relative
from .hardware_interface import exact_keys, strings, text
from .netclass_completeness import load_json
from .storage import digest, read_json, write_json

CLASSES = frozenset(('resistor', 'capacitor', 'inductor', 'ferrite', 'fuse', 'diode', 'switch'))


def anchor(value, *, pin=False):
    exact_keys(value, 'sheet uuid pin' if pin else 'sheet uuid')
    sheet = text(value['sheet'])
    if not sheet.startswith('/') or not sheet.endswith('/'):
        raise ValueError('expected native sheet UUID path')
    text(value['uuid'])
    if pin:
        text(value['pin'])
    return (sheet, value['uuid'])


def class_rule(rule):
    exact_keys(rule, 'class pins')
    if rule['class'] not in CLASSES:
        raise ValueError('unsupported two-terminal class')
    strings(rule['pins'])
    if len(rule['pins']) != 2:
        raise ValueError('two distinct physical pins required')


def validate_policy(policy):
    exact_keys(policy, 'schema requirement classes overrides exemptions rail_pairs')
    if policy['schema'] != 'agent-ee.netlist-extra-policy/1':
        raise ValueError('unsupported netlist-extra policy')
    exact_keys(policy['requirement'], 'id source')
    text(policy['requirement']['id'])
    exact_keys(policy['requirement']['source'], 'path locator')
    safe_relative(policy['requirement']['source']['path'])
    text(policy['requirement']['source']['locator'])
    if not isinstance(policy['classes'], dict):
        raise ValueError('expected exact library:symbol class map')
    for symbol, rule in policy['classes'].items():
        if ':' not in text(symbol):
            raise ValueError('class requires exact library:symbol ID')
        class_rule(rule)
    for category in ('overrides', 'exemptions', 'rail_pairs'):
        if not isinstance(policy[category], list):
            raise ValueError(f'{category} must be an explicit array')
    selectors = set()
    for override in policy['overrides']:
        exact_keys(override, 'component symbol rule')
        key = anchor(override['component'])
        text(override['symbol'])
        class_rule(override['rule'])
        if key in selectors:
            raise ValueError('duplicate override identity')
        selectors.add(key)
    exemptions = set()
    for exemption in policy['exemptions']:
        exact_keys(exemption, 'component symbol reason')
        key = anchor(exemption['component'])
        text(exemption['symbol'])
        text(exemption['reason'])
        if key in exemptions or key in selectors:
            raise ValueError('ambiguous exemption/override identity')
        exemptions.add(key)
    ids = set()
    for pair in policy['rail_pairs']:
        exact_keys(pair, 'id endpoints')
        pid = text(pair['id'])
        if pid in ids or not isinstance(pair['endpoints'], list) or len(pair['endpoints']) != 2:
            raise ValueError('rail pair requires unique ID and two endpoints')
        ids.add(pid)
        keys = [(*anchor(e, pin=True), e['pin']) for e in pair['endpoints']]
        if keys[0] == keys[1]:
            raise ValueError('rail endpoints must be distinct physical terminals')
    if not policy['classes'] and not policy['overrides'] and not policy['rail_pairs']:
        raise ValueError('policy has no topology predicates')
    return policy


def capability(params, scope, files, inputs, check_id):
    exact_keys(params, 'policy netlist_check')
    rel = str(safe_relative(params['policy']))
    if rel != params['policy'] or rel not in files or inputs is None:
        raise ValueError('topology policy must be captured')
    policy = validate_policy(load_json(Path(inputs) / rel))
    requirement = policy['requirement']
    mapped = [r for r in scope.get('baseline', {}).get('requirements', []) if check_id in r['checks']]
    if (len(mapped) != 1 or mapped[0]['id'] != requirement['id'] or
            mapped[0]['source'] != requirement['source'] or requirement['source']['path'] not in files):
        raise ValueError('topology policy requires exact captured requirement source and check mapping')
    predicates = ['netlist-extra:no-self-short'] if policy['classes'] or policy['overrides'] else []
    predicates += ['netlist-extra:rail-pair:' + p['id'] for p in policy['rail_pairs']]
    return {'policy':policy, 'immutable_inputs':[rel], 'predicates':predicates}


def evaluate(policy, graph):
    components, memberships, nets = (graph[k] for k in ('components', 'memberships', 'nets'))
    by_anchor = {(c['sheet'], c['uuid']): ref for ref, c in components.items()}
    overrides = {anchor(o['component']):o for o in policy['overrides']}
    exemptions = {anchor(e['component']):e for e in policy['exemptions']}
    records = []
    for ref, comp in sorted(components.items()):
        key = comp['sheet'], comp['uuid']
        record = {'kind':'component', 'reference':ref, 'sheet':comp['sheet'], 'uuid':comp['uuid'],
                  'symbol':comp['lib_id'], 'populated':comp['populated'], 'status':'unknown',
                  'locator':comp['locator']}
        records.append(record)
        if key in exemptions:
            exemption = exemptions[key]
            record['exemption'] = exemption
            if comp['lib_id'] == exemption['symbol']:
                record['status'] = 'excluded'
            else:
                record['reason'] = 'exemption symbol differs from native identity'
            continue
        override = overrides.get(key)
        rule = override['rule'] if override else policy['classes'].get(comp['lib_id'])
        if override and override['symbol'] != comp['lib_id']:
            record['reason'] = 'override symbol differs from native identity'
            continue
        if rule is None:
            record['reason'] = 'unknown exact symbol class'
            continue
        record['class'] = rule['class']
        record['pins'] = rule['pins']
        if set(comp['pins']) != set(rule['pins']):
            record['reason'] = 'declared two-terminal pins differ from complete native pin inventory'
            continue
        codes = [memberships.get((ref, pin)) for pin in rule['pins']]
        record['terminals'] = [{'reference':ref, 'pin':pin, 'net_id':code,
            'net_name':nets[code]['name'] if code is not None else None}
            for pin, code in zip(rule['pins'], codes)]
        if None in codes:
            record['reason'] = 'physical terminal has no exported net membership'
            continue
        record['status'] = 'violation' if codes[0] == codes[1] else 'satisfied'
    for category, declarations in (('override', overrides), ('exemption', exemptions)):
        for key in sorted(declarations.keys() - by_anchor.keys()):
            records.append({'kind':category, 'sheet':key[0], 'uuid':key[1], 'status':'unknown',
                            'reason':'declared component anchor absent from native inventory'})
    for pair in policy['rail_pairs']:
        record = {'kind':'rail_pair', 'id':pair['id'], 'status':'unknown', 'terminals':[]}
        records.append(record)
        for endpoint in pair['endpoints']:
            ref = by_anchor.get(anchor(endpoint, pin=True))
            code = memberships.get((ref, endpoint['pin']))
            record['terminals'].append({**endpoint, 'reference':ref, 'net_id':code,
                'net_name':nets[code]['name'] if code is not None else None})
        codes = [t['net_id'] for t in record['terminals']]
        if None in codes:
            record['reason'] = 'rail anchor or native membership unresolved'
            continue
        record['status'] = 'violation' if codes[0] == codes[1] else 'satisfied'
        record['locators'] = [nets[code]['locator'] for code in sorted(set(codes))]
        if record['status'] == 'violation':
            record['witness'] = {'net_id':codes[0], 'net_name':nets[codes[0]]['name'],
                'members': sorted([list(t) for t in nets[codes[0]]['terminals']])}
    groups = defaultdict(list)
    for nid, net in sorted(nets.items()):
        base = net['name'].rsplit('/', 1)[-1]
        groups[base].append({'net_id':nid, 'name':net['name'], 'locator':net['locator'],
            'sheets':sorted({components[ref]['sheet'] for ref, _ in net['terminals']})})
    advisories = [{'label':base, 'status':'informational', 'nets':entries}
                  for base, entries in sorted(groups.items()) if len(entries) > 1]
    complete = all(r['status'] != 'unknown' for r in records)
    violations = [r for r in records if r['status'] == 'violation']
    return {'records':records, 'advisories':advisories, 'complete':complete,
            'execution':'ok' if complete else 'unsupported',
            'outcome':('defect' if violations else 'pass') if complete else 'unknown'}


def check_netlist_extra(ctx):
    from .checks import CheckError, parse_xml_netlist
    from .datasheet_facts import prerequisite
    from .native_netlist import read_native_inventory
    from .netlist_parity import Unsupported
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
        specs = ctx.scope['checks']
        prior = next((s for s in specs[:specs.index(ctx.spec)]
                      if s['id'] == ctx.params['netlist_check'] and s['kind'] == 'netlist'), None)
        if prior is None:
            raise ValueError('native netlist prerequisite must precede topology check')
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    try:
        xml = prerequisite(ctx, prior['id'], 'netlist', 'design.xml')
        connectivity = prerequisite(ctx, prior['id'], 'netlist', 'connectivity.json')
        retained = load_json(connectivity)
        graph = read_native_inventory(xml)
        if retained != parse_xml_netlist(xml):
            raise ValueError('captured connectivity projection differs from strict native XML')
        producer = read_json(ctx.run_dir / 'checks' / f"{prior['id']}.json")
        if len(graph['sheets']) != producer['details']['sheet_instances']:
            raise ValueError('native XML hierarchy differs from producer coverage')
    except CheckError as exc:
        raise CheckError('failed', str(exc)) from exc
    except Unsupported as exc:
        raise CheckError('unsupported', str(exc)) from exc
    except (OSError, ValueError, TypeError, KeyError, ParseError) as exc:
        raise CheckError('failed', str(exc)) from exc
    report = evaluate(descriptor['policy'], graph)
    report.update(schema='agent-ee.netlist-extra-result/1', candidate_id=ctx.manifest['candidate_id'],
        netlist_check=prior['id'], native_xml_sha256=digest(xml), connectivity_sha256=digest(connectivity),
        policy_sha256=digest(ctx.inputs / ctx.params['policy']))
    write_json(ctx.work / 'topology.json', report)
    ctx.evidence('topology.json')
    ctx.result['details'].update(report)
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
        summary=f"Direct-net topology: {report['outcome']}; {len(report['advisories'])} informational label group(s)")
    ctx.result['limitations'].append('Only declared two-terminal classes and anchored direct-net equivalence. '
        'DNP requires explicit exemption. Native membership witnesses are not geometric wire/label paths. '
        'Same-basename hierarchical islands are informational; no physical, copper or impedance analysis.')
