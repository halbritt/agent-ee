"""Exact, bounded schematic graph comparison over independently captured inputs."""

from .check_registry import register as _register_check
import json
from collections import Counter
from pathlib import Path

from .hardware_interface import text, unique_object

from .design_limits import MAX_COMPONENTS, MAX_NETS, MAX_TERMINALS
MAX_BYTES = 8_000_000
MAX_SEARCH = 100_000
MAX_LIBRARY_BYTES = 32 * 1024 * 1024


class Unsupported(ValueError):
    """Well-formed input whose declared comparison cannot be established."""


def terminal_inventory(components, nets):
    memberships = {}
    for nid, net in nets.items():
        terminals = net['terminals']
        if not terminals or len(terminals) != len(set(terminals)):
            raise ValueError('empty net or duplicate terminal')
        for cid, pin in terminals:
            if cid not in components or pin not in components[cid]['pins']:
                raise ValueError('unknown component or physical pin')
            if (cid, pin) in memberships:
                raise ValueError('physical pin belongs to multiple nets')
            memberships[cid, pin] = nid
    if sum(len(c['pins']) for c in components.values()) > MAX_TERMINALS:
        raise Unsupported('terminal inventory limit exceeded')
    if not components:
        raise ValueError('empty component inventory')
    if len(components) > MAX_COMPONENTS:
        raise Unsupported('component inventory limit exceeded')
    if len(nets) > MAX_NETS:
        raise Unsupported('net inventory limit exceeded')
    return {'components': components, 'nets': nets, 'memberships': memberships}


def resolved_inventory(record):
    from .connectivity_ir import validate_resolved
    if (record.get('execution'), record.get('outcome'), record.get('coverage')) != ('ok', 'pass', 'complete'):
        raise Unsupported('resolution incomplete or rejected')
    validate_resolved(record)
    components, refs, nets = {}, set(), {}
    for comp in record['components']:
        cid, ref = text(comp['id']), text(comp['reference'])
        if cid in components or ref in refs:
            raise ValueError('duplicate resolved component identity')
        refs.add(ref)
        pins = [text(p['number']) for p in comp['pins']]
        if not pins or len(pins) != len(set(pins)):
            raise ValueError('missing or duplicate resolved physical pins')
        components[cid] = {'lib_id': text(comp['lib_id']), 'value': comp['value'],
            'populated': True, 'pins': tuple(sorted(pins)), 'reference': ref,
            'locator': f'components[id={cid}]'}
    for net in record['nets']:
        nid = text(net['id'])
        if nid in nets:
            raise ValueError('duplicate resolved net identity')
        terminals = [(text(t['component']), text(t['pin'])) for t in net['terminals']]
        if len(terminals) < 2:
            raise ValueError('resolved nets require two physical terminals')
        nets[nid] = {'name': net['name'], 'terminals': tuple(terminals),
                     'locator': f'nets[id={nid}]'}
    graph = terminal_inventory(components, nets)
    nc = [(text(t['component']), text(t['pin'])) for t in record['no_connects']]
    all_pins = {(cid, pin) for cid, comp in components.items() for pin in comp['pins']}
    if len(nc) != len(set(nc)) or set(nc) & graph['memberships'].keys():
        raise ValueError('duplicate or connected no-connect terminal')
    if set(nc) | graph['memberships'].keys() != all_pins:
        raise ValueError('resolved pin accounting incomplete')
    graph['no_connects'] = tuple(nc)
    return _physical_power_projection(graph, record)


def _physical_power_projection(graph, record):
    """KiCad omits virtual power/flag references from its physical XML netlist.

    Retain every physical terminal and record the omitted markers. Their
    presence and electrical drive declarations require native ERC separately.
    """
    virtual = {}
    for comp in record['components']:
        if not comp['power'] and not comp['reference'].startswith('#'):
            continue
        if (not comp['power'] or not comp['reference'].startswith('#') or comp['footprint']
                or len(comp['pins']) != 1 or len(comp['units']) != 1
                or comp['pins'][0]['electrical_type'] not in ('power_in', 'power_out')):
            raise Unsupported('unqualified virtual power symbol: ' + comp['id'])
        cid, pin = comp['id'], comp['pins'][0]['number']
        if (cid, pin) not in graph['memberships']:
            raise Unsupported('virtual power symbol must be connected: ' + cid)
        virtual[cid] = dict(component=cid, reference=comp['reference'], lib_id=comp['lib_id'], pin=pin,
                            net=graph['memberships'][cid, pin], locator=graph['components'][cid]['locator'])
    if not virtual:
        return graph
    nets = {}
    for nid, net in graph['nets'].items():
        terminals = tuple(t for t in net['terminals'] if t[0] not in virtual)
        if len(terminals) < 2:
            raise Unsupported('power projection requires two physical terminals per net: ' + nid)
        nets[nid] = {**net, 'terminals': terminals}
    components = {cid: comp for cid, comp in graph['components'].items() if cid not in virtual}
    return {**graph, **terminal_inventory(components, nets),
            'normalized_power_symbols': [virtual[cid] for cid in sorted(virtual)]}


def comparison_key(component):
    return (component['lib_id'], component['value'], component['populated'])


def _number_colors(signatures):
    labels = {signature: i for i, signature in enumerate(sorted(
        {s for graph in signatures for s in graph.values()}, key=repr))}
    return [{cid: labels[s] for cid, s in graph.items()} for graph in signatures]


def _refined_colors(expected, actual, component_anchors, net_anchors):
    """Joint refinement with only explicitly declared identity constraints."""
    graphs = (expected, actual)
    component_tags = ({cid: cid for cid in component_anchors},
                      {nid: cid for cid, nid in component_anchors.items()})
    net_tags = ({eid: eid for eid in net_anchors},
                {nid: eid for eid, nid in net_anchors.items()})
    colors = _number_colors([{cid: comparison_key(comp) + (comp['pins'],)
               + (tags.get(cid),) for cid, comp in graph['components'].items()}
               for graph, tags in zip(graphs, component_tags)])
    for _ in range(len(expected['components']) + len(actual['components'])):
        # Intern a net signature once, rather than allocating its full neighbour
        # tuple once per terminal (quadratic space on a high-fanout conductor).
        net_colors = _number_colors([{nid: (tags.get(nid), tuple(sorted((palette[cid], pin)
            for cid, pin in net['terminals']))) for nid, net in graph['nets'].items()}
            for graph, palette, tags in zip(graphs, colors, net_tags)])
        signatures = [{cid: (palette[cid], tuple((pin, nets[graph['memberships'][cid, pin]]
            if (cid, pin) in graph['memberships'] else -1) for pin in comp['pins']))
            for cid, comp in graph['components'].items()}
            for graph, palette, nets in zip(graphs, colors, net_colors)]
        updated = _number_colors(signatures)
        if len({c for palette in updated for c in palette.values()}) == len(
                {c for palette in colors for c in palette.values()}):
            return updated
        colors = updated
    return colors


def _net_sets(graph, mapping=None):
    return {nid: frozenset((mapping[cid] if mapping is not None else cid, pin)
            for cid, pin in net['terminals']) for nid, net in graph['nets'].items()}


def _assignments(candidates, budget):
    order = sorted(candidates, key=lambda cid: (len(candidates[cid]), cid))
    mapping, used = {}, set()

    # Explicit depth-first frames preserve candidate order and budget accounting
    # without consuming a Python call frame for every physical component.
    frames = [iter(candidates[order[0]])] if order else []
    if not order:
        yield {}
        return
    while frames:
        index = len(frames) - 1
        cid = order[index]
        if cid in mapping:
            used.remove(mapping.pop(cid))
        try:
            native = next(frames[-1])
        except StopIteration:
            frames.pop()
            continue
        if budget['steps'] >= budget['limit']:
            raise Unsupported('exact graph search limit exhausted')
        budget['steps'] += 1
        if native in used:
            continue
        mapping[cid] = native
        used.add(native)
        if len(frames) == len(order):
            yield dict(mapping)
        else:
            frames.append(iter(candidates[order[index + 1]]))


def _diagnostics(expected, actual, mapping):
    wanted, observed = _net_sets(expected, mapping), _net_sets(actual)
    matched = [{'expected': eid, 'native': nid} for eid, terminals in wanted.items()
               for nid, other in observed.items() if terminals == other]
    missing = [eid for eid in wanted if not any(m['expected'] == eid for m in matched)]
    extra = [nid for nid in observed if not any(m['native'] == nid for m in matched)]
    relations = {eid: [nid for nid, other in observed.items() if terminals & other]
                 for eid, terminals in wanted.items()}
    merged = [{'native': nid, 'expected': [eid for eid, nids in relations.items() if nid in nids]}
              for nid in observed if sum(nid in nids for nids in relations.values()) > 1]
    split = [{'expected': eid, 'native': nids} for eid, nids in relations.items() if len(nids) > 1]
    bindings = []
    for eid in missing:
        for cid, pin in expected['nets'][eid]['terminals']:
            native = mapping[cid]
            nid = actual['memberships'].get((native, pin))
            bindings.append({'component': cid, 'pin': pin, 'native_component': native,
                'expected_net': eid, 'native_net': nid,
                'kind': 'missing_binding' if nid is None or len(observed[nid]) == 1 else 'changed_binding',
                'intent_locator': expected['nets'][eid]['locator'],
                'native_locator': actual['components'][native]['locator'] + f', pin={pin}, ' +
                    (actual['nets'][nid]['locator'] if nid is not None else 'no net membership')})
    intersection = len(matched)
    precision = intersection / len(observed) if observed else float(not wanted)
    recall = intersection / len(wanted) if wanted else float(not observed)
    union = len(wanted) + len(observed) - intersection
    return {'matched_nets': matched, 'missing_nets': missing, 'extra_nets': extra,
        'merged_nets': merged, 'split_nets': split, 'bindings': bindings,
        'expected_net_names': {nid: n['name'] for nid, n in expected['nets'].items()},
        'native_net_names': {nid: n['name'] for nid, n in actual['nets'].items()},
        'scores': {'jaccard': intersection / union if union else 1.0,
                   'precision': precision, 'recall': recall,
                   'f1': 2 * precision * recall / (precision + recall) if precision + recall else 0.0}}


def _normalize_no_connects(expected, actual):
    declared = expected.get('no_connects', ())
    eligible = []
    for nid, net in actual['nets'].items():
        if not net.get('auto_unconnected_singleton') or len(net['terminals']) != 1:
            continue
        native, pin = net['terminals'][0]
        candidates = [cid for cid, number in declared if number == pin and
            comparison_key(expected['components'][cid]) == comparison_key(actual['components'][native])]
        if candidates:
            eligible.append({'native_net': nid, 'native_name': net['name'], 'terminal': [native, pin],
                'expected_candidates': candidates, 'native_no_connect_marker': net.get('no_connect_marker', False),
                'locator': net['locator']})
    ignored = {n['native_net'] for n in eligible}
    nets = {nid: net for nid, net in actual['nets'].items() if nid not in ignored}
    return {**actual, **terminal_inventory(actual['components'], nets)}, eligible


def _resolve_anchors(expected, actual, policy, name_policy, report):
    components, nets = {}, {}
    for cid, anchor in sorted(policy.get('anchors', {}).items()):
        if cid not in expected['components']:
            raise Unsupported('unknown anchored intent component')
        matches = [nid for nid, comp in actual['components'].items()
                   if all(comp.get(k) == v for k, v in anchor.items())]
        entry = {'expected': cid, 'identity': anchor, 'native': matches}
        report['anchors']['components'].append(entry)
        if len(matches) != 1:
            if 'uuid' in anchor:
                raise Unsupported('unresolvable sheet/UUID anchor')
            report['anchor_mismatches'].append({**entry, 'kind': 'component_identity_mismatch'})
            continue
        nid = matches[0]
        comp, native = expected['components'][cid], actual['components'][nid]
        if (comparison_key(comp), comp['pins']) != (comparison_key(native), native['pins']):
            report['anchor_mismatches'].append({**entry, 'kind': 'component_inventory_mismatch'})
        if nid in components.values():
            report['anchor_mismatches'].append({**entry, 'kind': 'component_anchor_conflict'})
        components[cid] = nid
    if name_policy is not None:
        from .net_names import basename, evaluate, validate_policy as validate_names
        validate_names(name_policy)
        required = {entry['name'] for entry in name_policy['required']}
        records = evaluate(name_policy, dict.fromkeys(required, 'required'), actual)['records']
        for record in records:
            if not record['owner_required']:
                continue
            name = record['name']
            wanted = [nid for nid, net in expected['nets'].items() if basename(net['name']) == name]
            if len(wanted) != 1:
                raise Unsupported('required net anchor absent or ambiguous in source: ' + name)
            native = [n['net_id'] for n in record['native_nets']]
            entry = {'name': name, 'expected': wanted[0], 'native': native,
                     'terminals': record['anchors']}
            report['anchors']['nets'].append(entry)
            if record['violations']:
                report['anchor_mismatches'].append({**entry, 'kind': 'required_name_mismatch',
                                                   'violations': record['violations']})
            elif record['unresolved']:
                raise Unsupported('required net terminal anchor unresolved: ' + name)
            else:
                nets[wanted[0]] = native[0]
    return components, nets


def compare(expected, actual, policy, *, name_policy=None):
    """Borrow inventories. Exhaustion/ambiguity is unknown, never an approximate pass."""
    predicate = policy['predicate']
    wanted = Counter(comparison_key(c) for c in expected['components'].values())
    observed = Counter(comparison_key(c) for c in actual['components'].values())
    missing, extra = wanted - observed, observed - wanted
    report = {'execution': 'ok', 'outcome': 'unknown', 'predicate': predicate,
        'missing_components': [{'key': list(k), 'count': v} for k, v in sorted(missing.items())],
        'extra_components': [{'key': list(k), 'count': v} for k, v in sorted(extra.items())],
        'expected_components': expected['components'], 'native_components': actual['components'],
        'component_matches': {}, 'correspondence_evaluated': False, 'search_steps': 0,
        'anchors': {'components': [], 'nets': []}, 'anchor_mismatches': []}
    if expected.get('normalized_power_symbols'):
        report['normalized_power_symbols'] = expected['normalized_power_symbols']
    if set(policy.get('instance_components', ())) - expected['components'].keys():
        report.update(execution='unsupported', reason='unresolved physical instance predicate')
        return report
    if predicate == 'components':
        report['outcome'] = 'defect' if missing or (extra and policy['multiset'] == 'exactly') else 'pass'
        return report
    if missing or extra:
        report['outcome'] = 'defect'
        return report
    actual, report['normalized_no_connects'] = _normalize_no_connects(expected, actual)
    candidates = {cid: sorted(nid for nid, native in actual['components'].items()
                  if comparison_key(comp) == comparison_key(native) and comp['pins'] == native['pins'])
                  for cid, comp in expected['components'].items()}
    budget = {'steps': 0, 'limit': policy.get('max_search', MAX_SEARCH)}
    try:
        component_anchors, net_anchors = _resolve_anchors(expected, actual, policy, name_policy, report)
        if report['anchor_mismatches']:
            report['outcome'] = 'defect'
            return report
        for cid, nid in component_anchors.items():
            candidates[cid] = [nid]
        if any(not matches for matches in candidates.values()):
            raise Unsupported('unresolvable component key, physical pin inventory or anchor')
        expected_colors, native_colors = _refined_colors(expected, actual, component_anchors, net_anchors)
        refined = {cid: [nid for nid in matches if expected_colors[cid] == native_colors[nid]]
                   for cid, matches in candidates.items()}
        anchored = bool(component_anchors or net_anchors)
        if anchored and Counter(expected_colors.values()) != Counter(native_colors.values()):
            # Joint refinement is a necessary condition for exact anchored isomorphism.
            # A contradicted constraint is a defect, without factorial diagnostic search.
            report.update(outcome='defect', anchor_mismatches=[{'kind': 'anchored_incidence_mismatch',
                'components': sorted(cid for cid, matches in refined.items() if not matches)}])
            return report
        observed_by_id = _net_sets(actual)
        observed_nets = set(observed_by_id.values())
        matches, best = {cid: set() for cid in candidates}, None
        for mapping in _assignments(refined, budget):
            mapped_nets = _net_sets(expected, mapping)
            if (set(mapped_nets.values()) == observed_nets and all(
                    mapped_nets[eid] == observed_by_id[nid] for eid, nid in net_anchors.items())):
                best = mapping
                for cid, nid in mapping.items():
                    matches[cid].add(nid)
                if predicate == 'topology':
                    break
        if best is None:
            if anchored:
                report.update(outcome='defect', search_steps=budget['steps'],
                    anchor_mismatches=[{'kind': 'anchored_bijection_mismatch'}])
                return report
            best_score = -1
            for mapping in _assignments(candidates, budget):
                score = len(set(_net_sets(expected, mapping).values()) & observed_nets)
                if score > best_score:
                    best, best_score = mapping, score
            if best is None:
                raise Unsupported('no compatible instance bijection')
            report['outcome'] = 'defect'
        else:
            report['topology_equivalent'] = True
            report['outcome'] = 'pass'
            if predicate == 'instance' and any(len(matches[cid]) != 1 for cid in policy['instance_components']):
                report.update(execution='unsupported', outcome='unknown',
                              reason='automorphism leaves required instance correspondence unresolved')
        report.update(_diagnostics(expected, actual, best))
        report['component_matches'] = {cid: sorted(nids) for cid, nids in matches.items()}
        report['correspondence_evaluated'] = predicate == 'instance' and report['execution'] == 'ok'
        report['search_steps'] = budget['steps']
    except Unsupported as exc:
        report.update(execution='unsupported', outcome='unknown', reason=str(exc), search_steps=budget['steps'])
    return report


def read_json_source(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise Unsupported('source byte limit exceeded')
    def invalid_constant(value):
        raise ValueError(f'non-finite JSON constant: {value}')
    try:
        return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except RecursionError as exc:
        raise ValueError('JSON nesting exceeds parser limit') from exc


def validate_policy(params):
    required = {'source', 'netlist_check', 'predicate', 'multiset'}
    if not isinstance(params, dict) or not required <= params.keys() or params.keys() - required - {
            'anchors', 'instance_components', 'max_search', 'net_name_policy'}:
        raise ValueError('expected source, netlist_check, predicate and multiset policy')
    source = params['source']
    if not isinstance(source, dict) or source.get('kind') not in ('resolved', 'intent', 'golden_xml'):
        raise ValueError('source requires explicit resolved, intent or golden_xml adapter')
    if source['kind'] == 'resolved' and set(source) != {'kind', 'path', 'intent'}:
        # A self-hashed resolution proves only its own structure, not that the
        # captured libraries produced it; its origin must be rederived.
        raise ValueError('resolved source requires the captured intent it was derived from')
    if source['kind'] != 'resolved' and set(source) != {'kind', 'path'}:
        raise ValueError('intent and golden_xml sources take exactly kind and path')
    if params['predicate'] not in ('topology', 'components', 'instance'):
        raise ValueError('unsupported parity predicate')
    if params['multiset'] not in ('exactly', 'at_least'):
        raise ValueError('unsupported component multiset policy')
    if params['multiset'] == 'at_least' and params['predicate'] != 'components':
        raise ValueError('at_least is supported only for component multiset predicates')
    anchors = params.get('anchors', {})
    if not isinstance(anchors, dict):
        raise ValueError('anchors must map logical IDs to explicit native identities')
    for cid, anchor in anchors.items():
        text(cid)
        if not isinstance(anchor, dict) or set(anchor) not in ({'sheet', 'uuid'}, {'reference'}):
            raise ValueError('anchors require sheet/UUID or an explicitly fixed reference')
        for value in anchor.values():
            text(value)
    if 'net_name_policy' in params:
        text(params['net_name_policy'])
    if (anchors or 'net_name_policy' in params) and params['predicate'] == 'components':
        raise ValueError('component multiset predicate does not evaluate instance anchors')
    if len({tuple(sorted(a.items())) for a in anchors.values()}) != len(anchors):
        raise ValueError('duplicate native anchor')
    instances = params.get('instance_components', [])
    if not isinstance(instances, list) or any(not isinstance(c, str) or not c for c in instances) or len(
            instances) != len(set(instances)):
        raise ValueError('instance_components must be unique logical IDs')
    if (params['predicate'] == 'instance') != bool(instances):
        raise ValueError('instance predicate requires explicit instance_components only')
    limit = params.get('max_search', MAX_SEARCH)
    if type(limit) is not int or not 1 <= limit <= MAX_SEARCH:
        raise ValueError('invalid bounded exact-search budget')
    return {**params, 'anchors': anchors, 'instance_components': instances, 'max_search': limit}


def capability(params, scope, files, inputs, check_id):
    from .candidate import safe_relative
    policy = validate_policy(params)
    source = policy['source']
    immutable = [str(safe_relative(source[key])) for key in ('path', 'intent') if key in source]
    if inputs is None or any(rel not in files for rel in immutable):
        raise ValueError('parity source and its intent must be captured')
    if source['kind'] != 'golden_xml':
        # Library dependencies come from the captured intent, never from an
        # authored resolution's own library claims.
        record = read_json_source(Path(inputs) / immutable[-1])
        for library in record['libraries']:
            path = str(safe_relative(library['path']))
            if path not in files or files[path] != library['sha256']:
                raise ValueError('library dependency missing or hash mismatch')
            immutable.append(path)
    specs = scope['checks']
    ids = [s['id'] for s in specs]
    prior = next((s for s in specs if s['id'] == params['netlist_check']), None)
    if prior is None or prior['kind'] != 'netlist' or ids.index(prior['id']) >= ids.index(check_id):
        raise ValueError('unresolved/forward/wrong-kind native netlist prerequisite')
    requirements = [r for r in scope.get('baseline', {}).get('requirements', []) if check_id in r['checks']]
    if len(requirements) != 1:
        raise ValueError('netlist_parity requires exactly one requirement per check')
    name_policy = None
    if 'net_name_policy' in policy:
        from .net_names import validate_policy as validate_names
        rel = str(safe_relative(policy['net_name_policy']))
        if rel not in files:
            raise ValueError('net-name anchor policy must be captured')
        name_policy = validate_names(read_json_source(Path(inputs) / rel))
        requirement = name_policy['requirement']
        if (requirement['source']['path'] not in files or not any(
                r['id'] == requirement['id'] and r['source'] == requirement['source']
                for r in scope['baseline']['requirements'])):
            raise ValueError('net-name anchor policy requires its captured baseline requirement source')
        immutable.extend([rel, requirement['source']['path']])
    return {'policy': policy, 'name_policy': name_policy, 'immutable_inputs': sorted(set(immutable)),
            'predicates': ['netlist-parity:' + policy['predicate']]}


def _resolve_captured_intent(inputs, rel):
    from .connectivity_ir import resolve_intent
    record = read_json_source(Path(inputs) / rel)
    if len(record['components']) > MAX_COMPONENTS:
        raise Unsupported('component inventory limit exceeded before resolution')
    if len(record.get('nets', ())) > MAX_NETS:
        raise Unsupported('net inventory limit exceeded before resolution')
    captures, remaining = {}, MAX_LIBRARY_BYTES
    for library in record['libraries']:
        with (Path(inputs) / library['path']).open('rb') as stream:
            raw = stream.read(remaining + 1)
        remaining -= len(raw)
        if remaining < 0:
            raise Unsupported('captured library byte limit exceeded')
        captures[library['path']] = raw
    return resolve_intent(record, captures)


def _source_inventory(inputs, policy):
    from .native_netlist import read_native_inventory
    source = policy['source']
    if source['kind'] == 'golden_xml':
        return read_native_inventory(Path(inputs) / source['path']), None
    record = _resolve_captured_intent(inputs, source.get('intent', source['path']))
    if (record.get('execution'), record.get('outcome'), record.get('coverage')) != ('ok', 'pass', 'complete'):
        return None, record
    if source['kind'] == 'resolved':
        from .connectivity_ir import canonical_resolved
        captured = read_json_source(Path(inputs) / source['path'])
        if canonical_resolved(captured) != canonical_resolved(record):
            raise ValueError('captured resolved record differs from coordinator rederivation '
                             'of its captured intent and libraries')
    return resolved_inventory(record), record


PROVENANCE = {'golden_xml': 'captured_golden_native_xml', 'intent': 'resolved_from_captured_intent',
              'resolved': 'captured_resolution_rederived_from_captured_intent'}


def check_netlist_parity(ctx):
    from .checks import CheckError, parse_xml_netlist
    from .datasheet_facts import prerequisite
    from .storage import digest, read_json, write_json
    from xml.etree.ElementTree import ParseError
    from .native_netlist import read_native_inventory
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
        producer = read_json(ctx.run_dir / 'checks' / f"{ctx.params['netlist_check']}.json")
        if producer.get('execution') != 'ok':
            raise CheckError('failed', 'native netlist producer did not complete successfully')
        try:
            xml = prerequisite(ctx, ctx.params['netlist_check'], 'netlist', 'design.xml')
            connectivity = prerequisite(ctx, ctx.params['netlist_check'], 'netlist', 'connectivity.json')
        except CheckError as exc:
            raise CheckError('failed', str(exc)) from exc
        expected, resolution = _source_inventory(ctx.inputs, descriptor['policy'])
        if resolution is not None:
            write_json(ctx.work / 'resolved-intent.json', resolution)
            ctx.evidence('resolved-intent.json')
        if expected is None:
            status = resolution.get('execution')
            raise CheckError(status if status in ('failed', 'malformed', 'unsupported') else 'unsupported',
                             'intent resolution incomplete or rejected; see resolved-intent.json')
        actual = read_native_inventory(xml)
        # The navigation projection must still be a valid product of this XML;
        # exact topology continues to use the full physical-pin inventory.
        if read_json_source(connectivity) != parse_xml_netlist(xml):
            raise ValueError('retained connectivity projection differs from native XML')
        if len(actual['sheets']) != producer['details']['sheet_instances']:
            raise ValueError('XML hierarchy count differs from native producer coverage')
        missing_ids = set(descriptor['policy']['instance_components']) - expected['components'].keys()
        if missing_ids:
            raise Unsupported(f'unresolved instance predicate: {sorted(missing_ids)}')
        report = compare(expected, actual, descriptor['policy'], name_policy=descriptor['name_policy'])
        if descriptor['name_policy'] is not None:
            report['net_name_policy_sha256'] = digest(ctx.inputs / ctx.params['net_name_policy'])
        report.update(schema='agent-ee.netlist-parity-result/1', candidate_id=ctx.manifest['candidate_id'],
            source_sha256=digest(ctx.inputs / ctx.params['source']['path']), native_xml_sha256=digest(xml),
            connectivity_sha256=digest(connectivity),
            intent_sha256=resolution['intent_sha256'] if resolution else None,
            source_provenance=PROVENANCE[ctx.params['source']['kind']],
            intent_source_sha256=digest(ctx.inputs / ctx.params['source']['intent'])
                if 'intent' in ctx.params['source'] else None)
        # Check results must equal their JSON receipt at sealing, including arrays.
        report = json.loads(json.dumps(report))
        write_json(ctx.work / 'parity.json', report)
        ctx.evidence('parity.json')
        ctx.result['details'].update(report)
        ctx.result['details']['evaluated_predicates'] = (
            descriptor['predicates'] if report['execution'] == 'ok' else [])
        ctx.result.update(execution=report['execution'], outcome=report['outcome'],
                          summary=f"Exact {ctx.params['predicate']} parity: {report['outcome']}")
        ctx.result['limitations'].append('Exact schematic incidence and component counts only. '
            'Scores are descriptive; electrical adequacy, footprint suitability and PCB routing are outside coverage. '
            'Automorphic instance correspondence requires explicit immutable anchors.')
        if report.get('normalized_power_symbols'):
            ctx.result['limitations'].append('Virtual power/flag symbols are omitted by native XML and '
                'listed in normalized_power_symbols. Parity checks physical connectivity; '
                'it cannot establish the presence of these markers or their ERC drive declarations.')
    except Unsupported as exc:
        raise CheckError('unsupported', str(exc)) from exc
    except OSError as exc:
        raise CheckError('failed', str(exc)) from exc
    except (ValueError, KeyError, TypeError, ParseError) as exc:
        raise CheckError('malformed', str(exc)) from exc


def describe_netlist_parity(ctx):
    descriptor = capability(ctx.params, ctx.scope, ctx.files, ctx.inputs, ctx.spec['id'])
    ctx.prerequisite(ctx.params.get('netlist_check'), ('netlist',))
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('netlist_parity', describe_netlist_parity, check_netlist_parity)
