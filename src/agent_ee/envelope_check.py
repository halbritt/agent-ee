"""One exact envelope field per native check; absent authority stays unknown.

Saved structural facts complement a read-only pcbnew load, never replace it.
Nominal copper ounces require a separately qualified manufacturing authority;
no thickness-to-weight conversion is inferred here.
"""
from decimal import Decimal
import re

from . import board_envelope as envelope
from .placement_check import _captured, _path
from .storage import write_json
from .symbol_library import children, parse_sexpr, nanometres

CRITERIA = {'outline/1': 'outline', 'layers/1': 'layer_count', 'stackup/1': 'stackup'}


def parameters(params):
    if not isinstance(params, dict) or set(params) != {'criterion', 'envelope'}:
        raise ValueError('board_envelope params require exactly criterion and envelope')
    if not isinstance(params['criterion'], str) or params['criterion'] not in CRITERIA:
        raise ValueError(f'board_envelope criterion must be one of {sorted(CRITERIA)}')
    return [_path(params['envelope'], 'envelope')]


def predicate_names(params):
    parameters(params)
    return [f"envelope:{params['criterion']}"]


def one(node, key):
    found = children(node, key)
    if len(found) != 1:
        raise ValueError(f'native {key} must occur exactly once')
    return found[0]


def token(node, key):
    found = one(node, key)
    if len(found) != 2 or not isinstance(found[1], str):
        raise ValueError(f'native {key} must have exactly one scalar')
    return found[1]


def saved_structure(pcb):
    """Retain missing/malformed authority independently for each field."""
    root = parse_sexpr(pcb)
    if root[0] != 'kicad_pcb':
        raise ValueError('expected kicad_pcb')
    result = {}
    try:
        # Reuse the exhaustive recursive Edge.Cuts inventory (including fields).
        facts = envelope.board_facts(pcb)
        result['outline'] = {k: facts[k] for k in ('edge_segments', 'other_edge_cuts')}
    except (ValueError, TypeError, IndexError) as exc:
        result['outline'] = {'unavailable': str(exc)}
    try:
        table = one(root, 'layers')
        names, ids, copper = set(), set(), []
        for layer in table[1:]:
            if (not isinstance(layer, list) or len(layer) not in (3, 4)
                    or any(not isinstance(x, str) for x in layer)
                    or not re.fullmatch(r'0|[1-9][0-9]*', layer[0])):
                raise ValueError('malformed native layer table row')
            lid, name, kind = int(layer[0]), layer[1], layer[2]
            if lid in ids or name in names:
                raise ValueError('duplicate native layer id or name')
            names.add(name)
            ids.add(lid)
            if re.fullmatch(r'F\.Cu|B\.Cu|In[1-9][0-9]?\.Cu', name):
                if kind not in ('signal', 'power', 'mixed'):
                    raise ValueError('unsupported copper layer type')
                copper.append({'id': lid, 'name': name})
            elif kind != 'user':
                raise ValueError('unknown native copper layer identity')
        if len(copper) < 2 or [l['name'] for l in copper] != sorted(
                [l['name'] for l in copper], key=envelope.copper_order):
            raise ValueError('missing or reordered native copper layers')
        result['layers'] = {'copper': copper}
    except ValueError as exc:
        result['layers'] = {'unavailable': str(exc)}
    try:
        total = nanometres(token(one(root, 'general'), 'thickness'))
        setup = one(root, 'setup')
        stacks = children(setup, 'stackup')
        if len(stacks) != 1:
            raise ValueError('missing or duplicate saved stackup')
        layers = []
        for layer in children(stacks[0], 'layer'):
            if len(layer) < 3 or not isinstance(layer[1], str):
                raise ValueError('malformed stackup layer')
            kind = token(layer, 'type')
            if kind == 'copper' or kind in ('core', 'prepreg'):
                thickness = nanometres(token(layer, 'thickness'))
                if thickness <= 0:
                    raise ValueError('nonpositive stackup thickness')
                # Multiple dielectric sublayers are outside this qualified domain.
                if children(layer, 'addsublayer'):
                    raise ValueError('dielectric sublayers unsupported')
                layers.append({'name': layer[1], 'kind': 'copper' if kind == 'copper' else 'dielectric',
                               'thickness_mm': envelope.mm(thickness)})
            elif (layer[1], kind) not in (
                    ('F.Paste', 'Top Solder Paste'), ('B.Paste', 'Bottom Solder Paste'),
                    ('F.Mask', 'Top Solder Mask'), ('B.Mask', 'Bottom Solder Mask'),
                    ('F.SilkS', 'Top Silk Screen'), ('B.SilkS', 'Bottom Silk Screen')):
                raise ValueError(f'unqualified native stackup layer {layer[1]!r}/{kind!r}')
        if len({l['name'] for l in layers}) != len(layers) or not layers:
            raise ValueError('missing or duplicate structural stackup layer')
        result['stackup'] = {'thickness_mm': envelope.mm(total), 'layers': layers}
    except ValueError as exc:
        result['stackup'] = {'unavailable': str(exc)}
    return result


def compare_saved(criterion, record, facts):
    field = CRITERIA[criterion]
    source = facts[{'layer_count': 'layers'}.get(field, field)]
    defects, unknown = [], []
    if 'unavailable' in source:
        unknown.append(source['unavailable'])
    elif field == 'outline':
        if source['other_edge_cuts']:
            unknown.append('unqualified Edge.Cuts geometry: ' + ', '.join(source['other_edge_cuts']))
        else:
            declared = [frozenset((envelope.point([Decimal(x) for x in e['start_mm']], 'start'),
                                  envelope.point([Decimal(x) for x in e['end_mm']], 'end')))
                        for e in record['outline']['edges']]
            saved = [frozenset(tuple(p) for p in s) for s in source['edge_segments']]
            if sorted(map(sorted, declared)) != sorted(map(sorted, saved)):
                defects.append('saved Edge.Cuts inventory differs from exact envelope polygon')
    elif field == 'layer_count':
        if [l['name'] for l in source['copper']] != record['copper_layers']:
            defects.append('saved copper layer set differs from exact envelope layer set')
    else:
        expected = {'thickness_mm': record['stackup']['thickness_mm'], 'layers': [
            {k: v for k, v in layer.items() if k != 'copper_weight_oz'} for layer in record['stackup']['layers']]}
        if source != expected:
            defects.append('saved structural stackup differs in name, order, kind or exact thickness')
    if field == 'stackup':
        unknown.append('nominal copper_weight_oz has no qualified captured manufacturing authority; '
                       'KiCad thickness does not establish ounces')
    return {'criterion': criterion, 'status': 'unknown' if unknown else 'defect' if defects else 'pass',
            'defects': defects, 'unknown': unknown, 'saved': source}


def check_board_envelope(ctx):
    from .checks import CheckError
    try:
        parameters(ctx.params)
        record = envelope.validate(_captured(ctx, ctx.params['envelope']))
        raw = _captured(ctx, ctx.scope['board'])
        facts = saved_structure(raw)
    except (ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'envelope/native input refused: {exc}') from exc
    ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.envelope_probe',
                 ctx.candidate_path(ctx.scope['board']), '--out', '/work/envelope-probe.json'], timeout=600)
    ctx.evidence('envelope-probe.json')
    native = ctx.load_json('envelope-probe.json', 'native envelope probe')
    if (not isinstance(native, dict) or native.get('schema') != 'agent-ee.envelope-probe/1'
            or native.get('board_sha256') != ctx.manifest['files'][ctx.scope['board']]
            or not isinstance(native.get('pcbnew_version'), str) or not native['pcbnew_version']):
        raise CheckError('malformed', 'native envelope probe identity is missing or differs from captured board')
    criterion = ctx.params['criterion']
    result = compare_saved(criterion, record, facts)
    if criterion == 'layers/1' and 'unavailable' not in facts['layers']:
        if native.get('copper') != facts['layers']['copper']:
            result['unknown'].append('native enabled copper differs from saved layer table')
    if criterion == 'outline/1':
        outline = native.get('outline', {})
        if outline.get('status') != 'proven':
            result['unknown'].append('native outline is not one proven straight closed contour')
        elif 'unavailable' not in facts['outline'] and not facts['outline']['other_edge_cuts']:
            native_edges = []
            for polygon in outline['polygons']:
                native_edges.extend(frozenset((tuple(p), tuple(polygon[(i + 1) % len(polygon)])))
                                    for i, p in enumerate(polygon))
            saved_edges = [frozenset(tuple(p) for p in s) for s in facts['outline']['edge_segments']]
            if sorted(map(sorted, native_edges)) != sorted(map(sorted, saved_edges)):
                result['unknown'].append('native outline differs from saved Edge.Cuts inventory')
    if criterion == 'stackup/1' and 'unavailable' not in facts['stackup']:
        if native.get('has_stackup') is not True:
            result['unknown'].append('native load did not retain an explicit stackup')
        if native.get('thickness_nm') != envelope.length(Decimal(facts['stackup']['thickness_mm']), 'total'):
            result['unknown'].append('native thickness differs from saved board thickness')
    if result['unknown']:
        result['status'] = 'unknown'
    judgment = {'schema': 'agent-ee.envelope-judgment/1', 'candidate_id': ctx.manifest['candidate_id'],
                'board_sha256': native['board_sha256'], 'pcbnew_version': native['pcbnew_version'],
                'envelope': {'path': ctx.params['envelope'], 'sha256': record['envelope_sha256']}, **result}
    write_json(ctx.work / 'envelope-judgment.json', judgment)
    ctx.evidence('envelope-judgment.json')
    ctx.result['details'].update(judgment)
    if result['status'] == 'unknown':
        raise CheckError('unsupported', '; '.join(result['unknown']))
    ctx.result.update(execution='ok', outcome=result['status'],
                      summary=f"{criterion}: {len(result['defects'])} exact envelope mismatch(es)")
