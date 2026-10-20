"""One exact envelope field per native check; absent authority stays unknown.

Saved structural facts complement a read-only pcbnew load, never replace it.
Nominal copper ounces require a separately qualified manufacturing authority;
no thickness-to-weight conversion is inferred here.
"""
from decimal import Decimal
from fractions import Fraction
import re

from . import board_envelope as envelope
from .placement_check import _captured, _path
from .storage import write_json
from .symbol_library import children, parse_sexpr, nanometres
from .placement import qualified_polygons

CRITERIA = {'outline/1': 'outline', 'layers/1': 'layer_count', 'stackup/1': 'stackup',
            'mounting-holes/1': 'mounting_holes', 'keepouts/1': 'keepouts',
            'component-height/1': 'max_component_height_mm'}


def parameters(params):
    if not isinstance(params, dict):
        raise ValueError('board_envelope params must be an object')
    if not isinstance(params.get('criterion'), str) or params['criterion'] not in CRITERIA:
        raise ValueError(f'board_envelope criterion must be one of {sorted(CRITERIA)}')
    selected = params['criterion'] in ('mounting-holes/1', 'keepouts/1')
    if params['criterion'] == 'component-height/1':
        from .component_height import parameters as height_parameters
        paths = height_parameters(params)
        return [_path(params['envelope'], 'envelope'), *paths]
    if set(params) != {'criterion', 'envelope'} | ({'id'} if selected else set()):
        raise ValueError('board_envelope params require criterion, envelope and id for holes/keepouts only')
    if selected:
        envelope.name(params['id'], 'envelope item id')
    return [_path(params['envelope'], 'envelope')]


def predicate_names(params):
    parameters(params)
    return [f"envelope:{params['criterion']}" + (':' + params['id'] if 'id' in params else '')]


def selected_item(record, params):
    items = [item for item in record[CRITERIA[params['criterion']]] if item['id'] == params['id']]
    if len(items) != 1:
        raise ValueError(f"declared envelope item {params['id']} missing or ambiguous")
    return items[0]


def point_nm(values):
    return envelope.point([Decimal(v) for v in values], 'point')


def covers(saved, declared):
    return all(envelope.segment_inside(a, declared[(i + 1) % len(declared)], saved)
               for i, a in enumerate(declared))


def hole_verdict(hole, native):
    """Select the declared reference and corroborate the whole drilled circle."""
    defects, unknown = [], []
    matches = [fp for fp in native['footprints'] if fp['ref'] == hole['id']]
    measured = {'footprints': matches}
    if len(matches) != 1:
        defects.append('expected exactly one mounting footprint with this reference')
    else:
        fp = matches[0]
        at, tolerance = point_nm(hole['at_mm']), nanometres(hole['tolerance_mm'])
        if sum((a - b) ** 2 for a, b in zip(fp['position_nm'], at)) > tolerance ** 2:
            defects.append('footprint origin exceeds declared position tolerance')
        drilled = [p for p in fp['pads'] if any(p['drill_nm'])]
        if len(drilled) != 1:
            defects.append('expected exactly one drilled pad')
        else:
            pad, drill = drilled[0], nanometres(hole['drill_mm'])
            if pad['attribute'] != ('PTH' if hole['plated'] else 'NPTH'):
                defects.append('drilled pad plating differs from declaration')
            if pad['drill_nm'] != [drill, drill]:
                defects.append('round drill diameter differs from drill_mm')
            # PAD.GetPosition is the drill anchor; GetOffset moves only the copper shape.
            if sum((a - b) ** 2 for a, b in zip(pad['position_nm'], at)) > tolerance ** 2:
                defects.append('drilled pad centre exceeds declared position tolerance')
            try:
                outline, = qualified_polygons(native['outline'])
                centre = tuple(pad['position_nm'])
                if not envelope.strictly_inside(centre, outline) or any(
                        4 * envelope.distance2(centre, a, outline[(i + 1) % len(outline)]) < drill ** 2
                        for i, a in enumerate(outline)):
                    defects.append('drilled circle is not inside the native outline')
            except ValueError as exc:
                unknown.append('native hole containment unavailable: ' + str(exc))
    return item_verdict(hole['id'], defects, unknown, measured)


def antenna_polygon(declaration, native, verified_facts):
    """Legacy dynamic declarations retain an explicit source-qualification hold."""
    from .footprint_library import derive_antenna_keepout
    return derive_antenna_keepout(verified_facts)


def item_verdict(item_id, defects, unknown, measured):
    return {'id': item_id, 'status': 'unknown' if unknown else 'defect' if defects else 'pass',
            'defects': defects, 'unknown': unknown, 'saved': measured}


def keepout_verdict(keepout, native, verified_facts=None):
    defects, unknown, candidates = [], [], []
    measured = {'rule_areas': candidates}
    owner = None
    try:
        if 'antenna' in keepout:
            declared, owner, derivation = antenna_polygon(keepout['antenna'], native, verified_facts)
            measured['derivation'] = derivation
        else:
            declared = [point_nm(p) for p in keepout['polygon_mm']]
        measured['declared_polygon_nm'] = [list(p) for p in declared]
    except ValueError as exc:
        return item_verdict(keepout['id'], [], [str(exc)], measured)
    for area in native['rule_areas']:
        if owner is not None and area['owner'] != owner:
            continue
        if not set(keepout['layers']) <= set(area['layers']):
            continue
        forbid = {('copper_pours' if k == 'zone_fills' else k) for k, enabled in area['forbid'].items() if enabled}
        if not set(keepout['forbid']) <= forbid:
            continue
        if area['placement_area']:
            unknown.append(f"rule area {area['uuid']} has unqualified placement-area semantics")
            continue
        try:
            polygon, = qualified_polygons(area['outline'])
            candidates.append({'uuid': area['uuid'], 'owner': area['owner'], 'covers': covers(polygon, declared)})
        except ValueError as exc:
            unknown.append(f"rule area {area['uuid']} cannot be compared: {exc}")
    if any(c['covers'] for c in candidates):
        unknown = []  # One fully proven area suffices; other areas cannot invalidate coverage.
    elif not unknown:
        defects.append('no saved rule area covers the declared polygon, layers and forbid set')
    return item_verdict(keepout['id'], defects, unknown, measured)


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
        # Sealing compares the returned check result with its retained JSON.
        result['outline'] = {'edge_segments': [[list(point) for point in segment]
                                              for segment in facts['edge_segments']],
                             'other_edge_cuts': facts['other_edge_cuts']}
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
        result['layers'] = {'copper': copper,
                            'types': {layer[1]: layer[2] for layer in table[1:] if layer[1] in names}}
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
        authority = record['fab_profile'].get('stackup', {})
        if authority.get('execution') != 'ready':
            expected = {'thickness_mm': record['stackup']['thickness_mm'], 'layers': [
                {k: v for k, v in layer.items() if k not in ('copper_weight_oz', 'role')}
                for layer in record['stackup']['layers']]}
            if source != expected:
                defects.append('saved structural stackup differs in name, order, kind or exact thickness')
        else:
            defects.extend(compare_stackup(record['stackup'], source, authority['expected']))
    if field in ('layer_count', 'stackup'):
        for layer in record['stackup']['layers']:
            if 'role' in layer:
                layers = facts['layers']
                if 'unavailable' in layers:
                    unknown.append(layers['unavailable'])
                elif layers['types'].get(layer['name']) != ('power' if layer['role'] == 'plane' else 'signal'):
                    defects.append(f"saved copper role differs for {layer['name']}")
    if field == 'stackup':
        reason = envelope.stackup_authority_reason(record)
        if reason:
            unknown.append(reason)
    return {'criterion': criterion, 'status': 'unknown' if unknown else 'defect' if defects else 'pass',
            'defects': defects, 'unknown': unknown, 'saved': source}


def compare_stackup(declared, saved, profile):
    """Compare exact rational thickness bounds and explicitly declared copper weights."""
    defects = []
    if declared['thickness_mm'] != profile['nominal_thickness_mm']:
        defects.append('envelope nominal thickness differs from profile stackup')
    if saved['thickness_mm'] != declared['thickness_mm']:
        defects.append('saved nominal board thickness differs from envelope')
    tolerance = Fraction(profile['layer_thickness_tolerance_mm'])
    expected = profile['layers']
    for label, layers in (('envelope', declared['layers']), ('saved', saved['layers'])):
        if [(l['name'], l['kind']) for l in layers] != [(l['name'], l['kind']) for l in expected]:
            defects.append(f'{label} stackup layer names, order or kinds differ from profile')
            continue
        for layer, bound in zip(layers, expected):
            if abs(Fraction(layer['thickness_mm']) - Fraction(bound['thickness_mm'])) > tolerance:
                defects.append(f"{label} layer {layer['name']} thickness exceeds profile tolerance")
            if label == 'envelope' and layer.get('copper_weight_oz') != bound.get('copper_weight_oz'):
                defects.append(f"envelope layer {layer['name']} copper_weight_oz differs from profile")
    return defects


def captured_record(scope, params, files, inputs):
    """Resolve manufacturing authority only through the scope's declared envelope profile."""
    from .board_envelope import captured_bytes
    raw = captured_bytes(files, inputs, params['envelope'], 'envelope')
    declared = scope.get('envelope') or {}
    profile = None
    if declared.get('path') == params['envelope'] and declared.get('profile') is not None:
        profile = captured_bytes(files, inputs, declared['profile'], 'profile')
    record = envelope.validate(raw, profile=profile, captured=(files, inputs))
    dependencies = {params['envelope'], *record['fab_profile'].get('source_paths', []),
                    *record['stackup'].get('source_paths', [])}
    if profile is not None:
        dependencies.add(declared['profile'])
    return record, sorted(dependencies)


def check_board_envelope(ctx):
    from .checks import CheckError
    try:
        parameters(ctx.params)
        record, _ = captured_record(ctx.scope, ctx.params, ctx.manifest['files'], ctx.inputs)
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
    if criterion == 'component-height/1':
        from .component_height import evaluate
        result = evaluate(ctx, record, native)
    elif criterion in ('mounting-holes/1', 'keepouts/1'):
        try:
            item = selected_item(record, ctx.params)
            if criterion == 'mounting-holes/1':
                result = hole_verdict(item, native)
            else:
                verified = None
                if 'antenna' in item:
                    from .datasheet_facts import prerequisite
                    from .storage import read_json
                    path = prerequisite(ctx, item['antenna']['facts_check'], 'datasheet_facts', 'facts.json',
                                        allow_unsupported=True)
                    verified = read_json(path)['facts']
                    if not verified or any(r.get('status') != 'verified' or 'fact' not in r for r in verified):
                        raise ValueError('antenna needs verified datasheet fact records')
                result = keepout_verdict(item, native, verified)
            result['criterion'] = criterion
        except (ValueError, KeyError, TypeError) as exc:
            raise CheckError('unsupported', f"{ctx.params['id']}: native envelope item unavailable: {exc}") from exc
    else:
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
                'envelope': {'path': ctx.params['envelope'], 'sha256': record['envelope_sha256']},
                'fab_profile': record['fab_profile'], **result}
    write_json(ctx.work / 'envelope-judgment.json', judgment)
    ctx.evidence('envelope-judgment.json')
    ctx.result['details'].update(judgment)
    if result['status'] == 'unknown':
        raise CheckError('unsupported', '; '.join(result['unknown']))
    label = criterion + (' ' + ctx.params['id'] if 'id' in ctx.params else '')
    ctx.result.update(execution='ok', outcome=result['status'],
                      summary=f"{label}: {len(result['defects'])} exact envelope mismatch(es)")
