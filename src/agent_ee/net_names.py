"""Required versus suggested net-name provenance over a captured native netlist.

Owner-declared (required) names come from an immutable policy bound to one
baseline requirement; the generator's name-level annotation is mutable
candidate input. The check never renames anything: suggested collisions get a
deterministic disambiguation proposal reported as information only.
"""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires
import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from .candidate import safe_relative
from .hardware_interface import exact_keys, text
from .netclass_completeness import load_json
from .schematic_emitter import GENERATED, NAME
from .storage import digest, write_json

LEVELS = frozenset(('required', 'suggested'))
# KiCad's own names for unlabelled nets carry no owner or generator name.
AUTO = re.compile(r'(?:/[^()]*/)?(?:Net|unconnected)-\(.*\)')
PAIR = ('_P', '_N')


def generated_name(value):
    if not isinstance(value, str) or not (NAME.fullmatch(value) or GENERATED.fullmatch(value)):
        raise ValueError(f'net name outside the generated-name grammar: {value!r}')
    return value


def basename(name):
    return name.rsplit('/', 1)[-1]


def validate_policy(policy):
    exact_keys(policy, 'schema requirement required')
    if policy['schema'] != 'agent-ee.net-name-policy/1':
        raise ValueError('unsupported net-name policy')
    exact_keys(policy['requirement'], 'id source')
    text(policy['requirement']['id'])
    exact_keys(policy['requirement']['source'], 'path locator')
    safe_relative(policy['requirement']['source']['path'])
    text(policy['requirement']['source']['locator'])
    if not isinstance(policy['required'], list):
        raise ValueError('required must be an explicit array')
    names, terminals = set(), set()
    for entry in policy['required']:
        exact_keys(entry, 'name anchors')
        name = generated_name(entry['name'])
        if name in names:
            raise ValueError(f'duplicate required name: {name}')
        names.add(name)
        if not isinstance(entry['anchors'], list) or not entry['anchors']:
            raise ValueError(f'required name {name} needs physical terminal anchors')
        for anchor in entry['anchors']:
            exact_keys(anchor, 'reference pin')
            key = text(anchor['reference']), text(anchor['pin'])
            # One terminal under two required names is a contradictory owner declaration.
            if key in terminals:
                raise ValueError(f'terminal {key[0]}.{key[1]} anchors more than one required name')
            terminals.add(key)
    return policy


def validate_levels(levels):
    exact_keys(levels, 'schema levels')
    if levels['schema'] != 'agent-ee.net-name-levels/1':
        raise ValueError('unsupported net-name level annotation')
    if not isinstance(levels['levels'], dict):
        raise ValueError('levels must map names to required or suggested')
    for name, level in levels['levels'].items():
        generated_name(name)
        if level not in LEVELS:
            raise ValueError(f'unsupported name level for {name}: {level!r}')
    return levels['levels']


def capability(params, scope, files, inputs, check_id):
    exact_keys(params, 'netlist_check policy levels')
    for key in ('policy', 'levels'):
        rel = str(safe_relative(params[key]))
        if rel != params[key] or rel not in files:
            raise ValueError(f'{key} must be captured')
    if params['policy'] == params['levels'] or inputs is None:
        raise ValueError('expected distinct captured policy and level annotation')
    policy = validate_policy(load_json(Path(inputs) / params['policy']))
    requirement = policy['requirement']
    mapped = [r for r in scope.get('baseline', {}).get('requirements', []) if check_id in r['checks']]
    if (len(mapped) != 1 or mapped[0]['id'] != requirement['id'] or
            mapped[0]['source'] != requirement['source'] or requirement['source']['path'] not in files):
        raise ValueError('net-name policy requires exact captured requirement source and check mapping')
    predicates = ['net-names:levels'] + sorted('net-names:required:' + r['name'] for r in policy['required'])
    return {'policy':policy, 'immutable_inputs':[params['policy']], 'predicates':predicates}


def disambiguate(base, entries, reserved, assigned):
    """Deterministic ``<stem>_<k>`` proposals in native-name order; ``_P``/``_N`` stay last.

    A pair index is used only when neither polarity already exists, so the two
    halves of a colliding pair on the same sheets receive the same index.
    """
    if base.endswith(PAIR) and len(base) > 2:
        stem, suffix, variants = base[:-2], base[-2:], PAIR
    else:
        stem, suffix, variants = base, '', ('',)
    proposals, index = [], 0
    for entry in entries:
        while True:
            index += 1
            candidate = f'{stem}_{index}{suffix}'
            if not NAME.fullmatch(candidate):
                return None
            if (candidate not in assigned and
                    all(f'{stem}_{index}{v}' not in reserved for v in variants)):
                break
        assigned.add(candidate)
        proposals.append({'net_id':entry['net_id'], 'name':entry['name'], 'proposed':candidate})
    return proposals


def evaluate(policy, levels, graph):
    components, memberships, nets = (graph[k] for k in ('components', 'memberships', 'nets'))
    by_base, unnamed = defaultdict(list), []
    for nid, net in sorted(nets.items(), key=lambda item: item[1]['name']):
        entry = {'net_id':nid, 'name':net['name'], 'locator':net['locator']}
        (unnamed if AUTO.fullmatch(net['name']) else by_base[basename(net['name'])]).append(entry)
    required = {r['name']:r for r in policy['required']}
    records = {}
    for name in sorted(set(by_base) | set(required) | set(levels)):
        records[name] = {'name':name, 'level':levels.get(name), 'owner_required':name in required,
                         'native_nets':by_base.get(name, []), 'violations':[], 'unresolved':[]}
    anchored = {}
    for name, declaration in required.items():
        record = records[name]
        record['anchors'] = []
        for anchor in declaration['anchors']:
            component = components.get(anchor['reference'])
            code = (memberships.get((anchor['reference'], anchor['pin']))
                    if component and anchor['pin'] in component['pins'] else None)
            record['anchors'].append({**anchor, 'net_id':code,
                                      'net_name':nets[code]['name'] if code is not None else None})
        if any(a['net_id'] is None for a in record['anchors']):
            record['unresolved'].append('anchor_unresolved')
        codes = sorted({a['net_id'] for a in record['anchors'] if a['net_id'] is not None})
        anchored[name] = set(codes)
        if not record['native_nets']:
            record['violations'].append('required_absent')
        if len(record['native_nets']) > 1:
            record['violations'].append('required_duplicate')
        if len(codes) > 1:
            record['violations'].append('required_split')
        if any(basename(nets[c]['name']) != name for c in codes):
            record['violations'].append('required_displaced')
        if record['level'] == 'suggested':
            record['violations'].append('level_downgraded')
    for first, second in combinations(sorted(required), 2):
        shared = sorted(anchored[first] & anchored[second])
        for name, other in ((first, second), (second, first)):
            if shared:
                record = records[name]
                if 'required_conflict' not in record['violations']:
                    record['violations'].append('required_conflict')
                record.setdefault('conflicts', []).append({'name':other, 'net_ids':shared,
                    'net_names':[nets[c]['name'] for c in shared]})
    reserved = set(by_base) | set(required) | set(levels)
    assigned = set()
    for name, record in records.items():
        if record['native_nets'] and record['level'] is None:
            record['unresolved'].append('level_missing')
        if record['level'] == 'required' and not record['owner_required']:
            record['violations'].append('required_unsupported')
        if record['level'] == 'suggested' and not record['owner_required'] and len(record['native_nets']) > 1:
            proposals = disambiguate(name, record['native_nets'], reserved, assigned)
            if proposals is None:
                record['unresolved'].append('disambiguation_unavailable')
            else:
                record['disambiguation'] = proposals
        if record['violations']:
            record['status'] = 'violation'
        elif record['unresolved']:
            record['status'] = 'unknown'
        elif 'disambiguation' in record:
            record.update(status='informational', severity='info')
        elif not record['native_nets']:
            record.update(status='informational', severity='info', note='annotated name absent from native netlist')
        else:
            record['status'] = 'satisfied'
        if record['violations'] and record['native_nets']:
            record['witness'] = {'net_ids':[n['net_id'] for n in record['native_nets']],
                                 'locators':[n['locator'] for n in record['native_nets']]}
    records = list(records.values())
    complete = all(not r['unresolved'] for r in records)
    violations = [r for r in records if r['violations']]
    return {'records':records, 'unnamed':unnamed, 'complete':complete,
            'disambiguated':[r['name'] for r in records if 'disambiguation' in r],
            'execution':'ok' if complete else 'unsupported',
            'outcome':('defect' if violations else 'pass') if complete else 'unknown'}


def check_net_names(ctx):
    from .checks import CheckError
    from .netlist_extra import native_graph
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    prior, xml, connectivity, graph = native_graph(ctx)
    path = ctx.inputs / ctx.params['levels']
    try:
        # The generator's annotation is candidate input; it is read, never repaired.
        levels = validate_levels(load_json(path))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('malformed', str(exc)) from exc
    report = evaluate(descriptor['policy'], levels, graph)
    report.update(schema='agent-ee.net-name-result/1', candidate_id=ctx.manifest['candidate_id'],
        netlist_check=prior['id'], native_xml_sha256=digest(xml), connectivity_sha256=digest(connectivity),
        policy_sha256=digest(ctx.inputs / ctx.params['policy']), levels_sha256=digest(path))
    write_json(ctx.work / 'net_names.json', report)
    ctx.evidence('net_names.json')
    ctx.result['details'].update(report)
    defects = sum(bool(r['violations']) for r in report['records'])
    ctx.result.update(execution=report['execution'], outcome=report['outcome'],
        summary=f"Net-name provenance: {report['outcome']}; {defects} name defect(s), "
                f"{len(report['disambiguated'])} suggested collision(s) reported as info")
    ctx.result['limitations'].append('Names are compared by native basename; anchors are exported '
        'reference/pin terminals. No label geometry, ERC or renaming. Suggested-collision proposals are '
        'information only; only _P/_N pair suffixes are recognized.')


def describe_net_names(ctx):
    descriptor = capability(ctx.params, ctx.scope, ctx.files, ctx.inputs, ctx.spec['id'])
    ctx.prerequisite(ctx.params.get('netlist_check'), ('netlist',))
    ctx.captured(ctx.params['levels'])
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('net_names', describe_net_names, check_net_names,
        early_prerequisites=(_requires.staged_placement_clear,), prerequisites=(_requires.stage_progress,))
