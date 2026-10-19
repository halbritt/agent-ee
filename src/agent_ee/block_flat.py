"""Flat intent composition and source-bound adaptation reports for captured blocks."""
import copy
import hashlib
import json
import re

from .connectivity_ir import _validate_flat_intent, resolve_intent
from .hardware_interface import exact_keys, text
from .schematic_emit import Quoted, format_sexpr
from .storage import digest
from .symbol_library import children

SCHEMA = 'agent-ee.flat-block-instance/1'


def _ranges(value):
    if not isinstance(value, dict) or not value:
        raise ValueError('reference ranges must be declared')
    for prefix, bounds in value.items():
        if (not re.fullmatch('[A-Z]+', prefix) or not isinstance(bounds, list) or len(bounds) != 2
                or any(type(n) is not int for n in bounds) or not 1 <= bounds[0] <= bounds[1] <= 999999):
            raise ValueError('invalid reference range')
    return value


def _references(plan, ranges):
    refs = sorted(r for s in plan['sources'] for r in s['references'].values())
    groups = {}
    for ref in refs:
        match = re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', ref)
        if not match:
            raise ValueError('source reference must be annotated')
        groups.setdefault(match[1], []).append(ref)
    if set(groups) != set(_ranges(ranges)):
        raise ValueError('reference ranges must cover selected prefixes exactly')
    result = {}
    for prefix, group in sorted(groups.items()):
        start, end = ranges[prefix]
        if len(group) > end - start + 1:
            raise ValueError('reference range exhausted')
        for number, old in enumerate(sorted(group, key=lambda s: int(s[len(prefix):])), start):
            result[old] = prefix + str(number)
    return result


def _bindings(plan, bindings):
    if not isinstance(bindings, dict) or set(bindings) != {p['name'] for p in plan['ports']}:
        raise ValueError('every boundary port must be bound')
    for name in bindings.values():
        text(name)
    for port in plan['ports']:
        if bindings[port['name']] != bindings[port['net_name']]:
            raise ValueError('boundary alias binding differs')
    canonical = [p['name'] for p in plan['ports'] if p['alias_of'] is None]
    if len({bindings[name] for name in canonical}) != len(canonical):
        raise ValueError('distinct boundary nets cannot be merged')


def _part_refs(plan):
    return {delivered: {'sheet': source['sheet'], 'instance': source['uuid_path'], 'reference': ref}
            for source in plan['sources'] for ref, delivered in sorted(source['references'].items())}


def _library_id(instance, original):
    library, symbol = original.split(':')
    # Hex encoding prevents different native names from collapsing into one namespace.
    return 'Block_' + instance.encode().hex() + '_' + library.encode().hex() + ':' + symbol


def _libraries(selected, instance):
    groups = {}
    versions = {}
    for item in selected:
        original = item['native']['lib_id']
        target, symbol = _library_id(instance, original).split(':')
        definition = next(n for n in children(children(item['tree'], 'lib_symbols')[0], 'symbol')
                          if n[1] == original)
        if children(definition, 'extends'):
            raise ValueError('inherited source definitions are unsupported')
        definition = copy.deepcopy(definition)
        definition[1] = Quoted(symbol)
        # Older source formats use '~' for an empty property. Preserve that
        # meaning when the definition is embedded in the modern schematic.
        source_version = int(children(item['tree'], 'version')[0][1])
        version = 20250318 if source_version >= 20250318 else 20241209
        versions[target] = max(versions.get(target, 20241209), version)
        if source_version < 20250318:
            for prop in children(definition, 'property'):
                if prop[2] == '~':
                    prop[2] = Quoted('')
        # Legacy pin_names stores the hide flag after offset. The existing emitter
        # serializes atoms before child lists; KiCad accepts either token order.
        for names in children(definition, 'pin_names'):
            if 'hide' in names[1:]:
                names[:] = [names[0], 'hide', *[n for n in names[1:] if n != 'hide']]
        # Preserve the native source's empty pin names rather than expanding old
        # KiCad '~' placeholders into new names during modern emission.
        for unit in children(definition, 'symbol'):
            for pin in children(unit, 'pin'):
                number = children(pin, 'number')[0][1]
                if number in item['native']['pin_metadata']:
                    children(pin, 'name')[0][1] = Quoted(item['native']['pin_metadata'][number]['name'])
        previous = groups.setdefault(target, {}).setdefault(symbol, definition)
        if previous != definition:
            raise ValueError('conflicting captured symbol definitions')
    manifests, captures = [], {}
    for name, definitions in sorted(groups.items()):
        tree = ['kicad_symbol_lib', ['version', str(versions[name])], ['generator', Quoted('agent-ee')],
                *[definitions[key] for key in sorted(definitions)]]
        data = ('\n'.join(format_sexpr(tree)) + '\n').encode()
        path = 'libraries/' + name + '.kicad_sym'
        captures[path] = data
        manifests.append({'name': name, 'path': path, 'sha256': hashlib.sha256(data).hexdigest()})
    return manifests, captures


def _component(item, instance, ref):
    native = item['native']
    if not all(native[key] for key in ('populated', 'on_board', 'in_bom')) or len(native['units']) != 1:
        raise ValueError('flat composition requires one populated native unit per component')
    fields = {p[1]: p[2] for p in children(item['node'], 'property')
              if p[1] not in ('Reference', 'Value', 'Footprint')}
    fields['Datasheet'] = native['datasheet']
    return {'id': instance + '/' + item['delivered'], 'reference': ref,
            'lib_id': _library_id(instance, native['lib_id']), 'value': native['value'],
            'footprint': native['footprint'], 'units': [1], 'part_binding': fields}


def compose(root, intent, blocks, bindings, captured_libraries):
    from .block_adaptation import _source, _local, _path
    from .block_composition import native_models
    from .findings import safe_id
    _validate_flat_intent(intent)
    if intent.get('hierarchy') or intent.get('ports'):
        raise ValueError('flat composition requires flat intent/1')
    if not isinstance(blocks, list) or not blocks or not isinstance(bindings, dict):
        raise ValueError('explicit blocks and bindings required')
    for request in blocks:
        exact_keys(request, 'id block composition reference_ranges sheet requirements')
    ids = [b['id'] for b in blocks]
    if len(set(ids)) != len(ids) or set(bindings) != set(ids):
        raise ValueError('unique block ids and exactly one binding map per block required')
    output, captures = copy.deepcopy(intent), dict(captured_libraries)
    records, reserved = [], []
    for request in sorted(blocks, key=lambda r: r['id']):
        exact_keys(request, 'id block composition reference_ranges sheet requirements')
        ident = request['id']; safe_id(ident, 'block instance id')
        _path(request['sheet'])
        source = _source(root, request['block'], request['composition'])
        plan = source['_composition']
        mapping = request['requirements']
        if (not isinstance(mapping, dict) or set(mapping) != {r['id'] for r in source['requirements']}
                or any(not isinstance(checks, list) or not checks or any(not isinstance(c, str) or not c for c in checks)
                       for checks in mapping.values())):
            raise ValueError('every source requirement needs a fresh check mapping')
        bound = bindings[ident]; _bindings(plan, bound)
        references = _references(plan, request['reference_ranges'])
        ranges = request['reference_ranges']
        for prefix, (start, end) in ranges.items():
            if any(p == prefix and max(start, a) <= min(end, b) for p, a, b in reserved):
                raise ValueError('block reference ranges collide')
            for comp in output['components']:
                match = re.fullmatch(re.escape(prefix) + r'([0-9]+)', comp['reference'])
                if match and start <= int(match[1]) <= end:
                    raise ValueError('reference collision in declared range')
            reserved.append((prefix, start, end))
        selected = native_models(root, request['block'], plan)
        manifests, new_captures = _libraries(selected, ident)
        if ({lib['name'] for lib in manifests} & {lib['name'] for lib in output['libraries']} or
                set(new_captures) & set(captures)):
            raise ValueError('captured library namespace collision')
        output['libraries'].extend(manifests); captures.update(new_captures)
        for item in sorted(selected, key=lambda i: i['delivered']):
            component = _component(item, ident, references[item['delivered']])
            if component['id'] in {c['id'] for c in output['components']}:
                raise ValueError('component id collision')
            output['components'].append(component)
            for pin, port in sorted(item['nets'].items()):
                name = bound[port]
                found = [net for net in output['nets'] if net['name'] == name]
                if len(found) > 1:
                    raise ValueError('ambiguous board net name')
                if not found:
                    found = [{'id': ident + '/net/' + port, 'name': name, 'terminals': []}]
                    output['nets'].extend(found)
                found[0]['terminals'].append({'component': component['id'], 'pin': {'number': pin}})
        records.append({'schema': SCHEMA, 'id': ident, 'block': request['block'],
            'block_id': source['id'], 'version': source['version'], 'sha256': source['files'][source['sheet']],
            'sheet': request['sheet'], 'instance_path': '/', 'bindings': copy.deepcopy(bound),
            'requirements': copy.deepcopy(request['requirements']),
            'composition': {'path': request['composition'], 'sha256': digest(_local(root, request['composition']))},
            'reference_ranges': copy.deepcopy(ranges), 'references': references, 'source_parts': _part_refs(plan)})
    for key in ('components', 'nets'):
        output[key].sort(key=lambda item: item['id'])
    output['libraries'].sort(key=lambda item: item['name'])
    for net in output['nets']:
        net['terminals'].sort(key=lambda t: (t['component'], repr(t['pin'])))
    resolved = resolve_intent(output, captures)
    if (resolved['execution'], resolved['outcome'], resolved['coverage']) != ('ok', 'pass', 'complete'):
        raise ValueError('composed intent cannot resolve: ' + repr(resolved['findings']))
    power = {c['id'] for c in resolved['components'] if c['power']}
    native_names = {n['name']: ('' if any(t['component'] in power for t in n['terminals']) else '/') + n['name']
                    for n in resolved['nets']}
    for record in records:
        record['native_bindings'] = {port: native_names[name] for port, name in record['bindings'].items()}
    return {'intent': output, 'captures': captures, 'block_instances': records}


def read_instance(root, record):
    """Validate immutable flat mapping; current design remains free to differ."""
    from .block_adaptation import _local, _path, _source
    from .findings import safe_id
    exact_keys(record, 'schema id block block_id version sha256 sheet instance_path bindings requirements '
               'composition reference_ranges references source_parts native_bindings')
    if record['schema'] != SCHEMA or record['instance_path'] != '/':
        raise ValueError('flat block instance requires native root occurrence')
    safe_id(record['id'], 'block instance id')
    _path(record['sheet']); _path(record['block'])
    composition = record['composition']
    exact_keys(composition, 'path sha256')
    if digest(_local(root, composition['path'])) != composition['sha256']:
        raise ValueError('flat composition plan hash differs')
    source = _source(root, record['block'], composition['path'])
    if (record['block_id'], record['version'], record['sha256']) != (
            source['id'], source['version'], source['files'][source['sheet']]):
        raise ValueError('flat block source identity differs')
    plan = source['_composition']
    if record['references'] != _references(plan, record['reference_ranges']):
        raise ValueError('flat reference mapping differs from declared ranges')
    if record['source_parts'] != _part_refs(plan):
        raise ValueError('flat source-part mapping differs')
    _bindings(plan, record['bindings'])
    native = record['native_bindings']
    if not isinstance(native, dict) or set(native) != set(record['bindings']):
        raise ValueError('native bindings must account for every port')
    for port, name in record['bindings'].items():
        if native[port] not in (name, '/' + name):
            raise ValueError('native binding differs from flat label naming')
    _bindings(plan, native)
    if not isinstance(record['requirements'], dict) or set(record['requirements']) != {r['id'] for r in source['requirements']}:
        raise ValueError('every block requirement needs a board check mapping')
    return record, source


def check(ctx, descriptor):
    """Compare native delivered graph to fresh source selection under immutable maps.

    Adaptation is information, never approval. Boundary failures remain defects.
    """
    from .block_adaptation import _export, _read
    from .block_composition import models
    from .checks import parse_xml_netlist
    from .datasheet_facts import prerequisite
    from .native_netlist import read_native_inventory
    from .schematic_lint import load_sheets
    from .storage import write_json
    from pathlib import PurePosixPath
    record, block = descriptor['instance'], descriptor['block']
    if record['sheet'] != ctx.scope['schematic']:
        raise ValueError('flat record must name the board root schematic')
    sheets = load_sheets(ctx.inputs, record['sheet'])
    if len(sheets) != 1 or sheets[0]['path'] != '/' or children(sheets[0]['tree'], 'sheet'):
        raise ValueError('flat provenance refuses hierarchical board subjects')
    xml = prerequisite(ctx, ctx.params['netlist_check'], 'netlist', 'design.xml')
    projection = prerequisite(ctx, ctx.params['netlist_check'], 'netlist', 'connectivity.json')
    if _read(projection) != parse_xml_netlist(xml):
        raise ValueError('board connectivity differs from native XML')
    actual = read_native_inventory(xml)
    producer = _read(ctx.run_dir / 'checks' / (ctx.params['netlist_check'] + '.json'))
    if len(actual['sheets']) != 1 or producer['details']['sheet_instances'] != 1:
        raise ValueError('flat native occurrence coverage differs')
    selected = models(ctx.inputs / record['block'], block['_composition'],
        lambda i, path: _export(ctx, 'source-' + str(i),
            '/candidate/' + str(PurePosixPath(record['block']) / path)))
    def identity(native, lib_id=None):
        return {**{k: native[k] for k in ('value', 'footprint', 'datasheet', 'populated', 'on_board',
                'in_bom', 'pins', 'pin_metadata', 'library_pins')},
                'lib_id': lib_id or native['lib_id'],
                'properties': {k: v for k, v in native['properties'].items() if k not in ('Sheetname', 'Sheetfile')}}
    changes, nets, boundary = [], [], []
    expected_refs = set(record['references'].values())
    expected_by_port = {}
    for item in selected:
        ref = record['references'][item['delivered']]
        before = identity(item['native'], _library_id(record['id'], item['native']['lib_id']))
        current = actual['components'].get(ref)
        after = identity(current) if current else None
        if before != after:
            changes.append({'reference': ref, 'source_part': record['source_parts'][item['delivered']],
                            'severity': 'adaptation', 'before': before, 'after': after})
        for pin, port in item['nets'].items():
            expected = record['native_bindings'][port]
            observed = actual['nets'].get(actual['memberships'].get((ref, pin)))
            name = observed['name'] if observed else None
            expected_by_port.setdefault(port, []).append({'reference': ref, 'pin': pin, 'observed_net': name})
            if name != expected:
                nets.append({'terminal': [ref, pin], 'severity': 'adaptation', 'before': expected, 'after': name})
    # New components inside an instance's reserved ranges are also adaptations.
    for ref, current in sorted(actual['components'].items()):
        match = re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', ref)
        if match and ref not in expected_refs and match[1] in record['reference_ranges']:
            start, end = record['reference_ranges'][match[1]]
            if start <= int(match[2]) <= end:
                changes.append({'reference': ref, 'source_part': None, 'severity': 'adaptation',
                                'before': None, 'after': identity(current)})
    for port in block['_composition']['ports']:
        observations = expected_by_port[port['net_name']]
        expected = record['native_bindings'][port['name']]
        valid = all(p['observed_net'] == expected for p in observations)
        boundary.append({'port': port['name'], 'binding': record['bindings'][port['name']],
            'expected_net': expected, 'terminals': observations,
            'status': 'satisfied' if valid else 'violation'})
    report = {'schema': 'agent-ee.block-provenance/1', 'mode': 'flat-intent',
        'candidate_id': ctx.manifest['candidate_id'], 'instance': record,
        'component_changes': changes, 'net_changes': nets, 'boundary': boundary,
        'composition': {'plan': record['composition'], 'sources': block['_composition']['sources'],
                        'source_commit': block['source']['commit']},
        'source_evidence': 'provenance_only', 'native_xml_sha256': digest(xml),
        'adaptation_authority': 'reported_only_not_approved'}
    report = json.loads(json.dumps(report))
    write_json(ctx.work / 'provenance.json', report); ctx.evidence('provenance.json')
    ctx.result['details'].update(report)
    defects = sum(p['status'] == 'violation' for p in boundary)
    ctx.result.update(execution='ok', outcome='defect' if defects else 'info' if changes or nets else 'pass',
        summary=f'flat block: {len(changes)} component and {len(nets)} pin-net adaptations; {defects} boundary defects')
    ctx.result['limitations'].append('Flat source comparison only. Adaptations are reported, not approved; '
        'source review, part decisions and fresh board requirements retain their own authority. '
        'Only recorded components and their reserved reference ranges are attributed to this block.')
