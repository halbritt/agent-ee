"""Additive factual navigation. No entry is acceptance or a check witness."""

import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from .candidate import safe_relative
from .findings import safe_id
from .storage import canonical, digest, identity, read_json, write_json

MAX_SOURCE_BYTES = 16_000_000
MAX_ENTITIES = 50_000


def _source(run, rel):
    rel = str(safe_relative(rel))
    path = run / rel
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('navigation source contains a symlink')
    if not path.resolve().is_relative_to(run.resolve()):
        raise ValueError('navigation source escapes run')
    return path


def _append_entity(entities, entry):
    if len(entities) >= MAX_ENTITIES:
        raise ValueError('native entity limit exceeded')
    entities.append(entry)


def _netlist(data, source):
    root = ET.fromstring(data)
    if root.tag != 'export' or root.get('version') not in ('D', 'E'):
        raise ValueError('unsupported native netlist root')
    entities, edges, diagnostics = [], [], []
    components = root.findall('./components/comp')
    refs = Counter(c.get('ref') for c in components)
    parts = {}
    for i, part in enumerate(root.findall('./libparts/libpart')):
        parts.setdefault((part.get('lib'), part.get('part')), []).append((i, part))
    by_ref, pin_lookup = {}, {}
    for i, comp in enumerate(components):
        ref = comp.get('ref')
        sheet = comp.find('sheetpath')
        native = [sheet.get('tstamps') if sheet is not None else None,
                  comp.findtext('tstamps'), ref]
        status = 'native' if native[0] and native[1] and refs[ref] == 1 else 'unresolved'
        locator = f'./components/comp[{i + 1}]'
        entry = {'kind': 'component', 'label': ref, 'native_identity': native,
                 'identity_status': status, 'source': {**source, 'locator': locator}}
        _append_entity(entities, entry)
        by_ref.setdefault(ref, []).append(entry)
        lib = comp.find('libsource')
        matches = parts.get((lib.get('lib'), lib.get('part')), []) if lib is not None else []
        if len(matches) != 1:
            diagnostics.append({'source': entry['source'], 'reason': 'missing/ambiguous libpart pin inventory'})
            continue
        part_index, part = matches[0]
        pins = part.findall('./pins/pin')
        counts = Counter(p.get('num') for p in pins)
        for j, pin in enumerate(pins):
            pin_entry = {'kind': 'pin', 'label': f'{ref}.{pin.get("num")}',
                'reference': ref, 'pin': pin.get('num'), 'parent_locator': locator,
                'native_identity': [native, pin.get('num')],
                'identity_status': status if counts[pin.get('num')] == 1 else 'unresolved',
                'connection': 'unconnected',
                'source': {**source, 'locator': f'./libparts/libpart[{part_index + 1}]/pins/pin[{j + 1}]'}}
            _append_entity(entities, pin_entry)
            edges.append((entry, pin_entry, pin_entry['source'], 'contains_pin'))
            pin_lookup.setdefault((ref, pin.get('num')), []).append(pin_entry)
    for i, net in enumerate(root.findall('./nets/net')):
        locator = f'./nets/net[{i + 1}]'
        entry = {'kind': 'net', 'label': net.get('name'),
            'native_identity': [net.get('code'), net.get('name')],
            'identity_status': 'fallback', 'source': {**source, 'locator': locator}}
        _append_entity(entities, entry)
        for j, node in enumerate(net.findall('node')):
            matches = pin_lookup.get((node.get('ref'), node.get('pin')), [])
            if len(by_ref.get(node.get('ref'), [])) != 1 or len(matches) != 1:
                diagnostics.append({'source': {**source, 'locator': f'{locator}/node[{j + 1}]'},
                                    'reason': 'unresolved native node identity', 'node': dict(node.attrib)})
                continue
            matches[0]['connection'] = 'connected'
            edges.append((matches[0], entry, {**source, 'locator': f'{locator}/node[{j + 1}]'}, 'net_membership'))
    return entities, edges, diagnostics


def _geometry(data, source):
    report = json.loads(data)
    if not isinstance(report, dict) or not isinstance(report.get('nets'), dict):
        raise ValueError('unsupported geometry net inventory')
    entities, diagnostics, edges, nets = [], [], [], {}
    for name in sorted(report['nets']):
        entry = {'kind': 'net', 'label': name, 'native_identity': ['board-net', name],
            'identity_status': 'fallback', 'source': {**source, 'locator': '/nets/' + name.replace('~', '~0').replace('/', '~1')}}
        _append_entity(entities, entry)
        nets[name] = entry
    for group in ('footprints', 'pads', 'tracks', 'zones'):
        if not isinstance(report.get(group), list):
            raise ValueError(f'unsupported geometry inventory: {group}')
        for i, item in enumerate(report[group]):
            if not isinstance(item, dict):
                raise ValueError('malformed geometry entity')
            kind = {'footprints': 'footprint', 'pads': 'pad', 'tracks': 'primitive', 'zones': 'zone'}[group]
            entry = {'kind': kind, 'label': item.get('ref', item.get('net')),
                'native_identity': [item.get('uuid'), item.get('layer', item.get('layers')), item.get('net')],
                'identity_status': 'native' if item.get('uuid') else 'unresolved',
                'native_class': item.get('native_class'),
                'source': {**source, 'locator': f'/{group}/{i}'}}
            _append_entity(entities, entry)
            if item.get('net') in nets:
                edges.append((entry, nets[item['net']], entry['source'], 'net_membership'))
            elif item.get('net'):
                diagnostics.append({'source': entry['source'], 'reason': 'missing geometry net identity'})
            if group == 'tracks' and item.get('native_class') not in ('PCB_TRACK', 'PCB_ARC', 'PCB_VIA'):
                diagnostics.append({'source': entry['source'], 'reason': 'unsupported primitive class'})
    counts = Counter(tuple(e['native_identity'][:1]) for e in entities if e['kind'] != 'net')
    for entry in entities:
        if entry['kind'] != 'net' and counts[tuple(entry['native_identity'][:1])] > 1:
            entry['identity_status'] = 'unresolved'
    return entities, edges, diagnostics


def build_index(run, manifest, baseline, checks, run_id):
    """Read only declared coordinator outputs, never reviewer files or prose."""
    run = Path(run)
    entries, diagnostics, sources = [], [], {}
    by_check = {c['id']: c for c in checks}
    entities, connections = [], []
    for position, spec in enumerate(manifest['scope'].get('checks', [])):
        cid, kind = spec['id'], spec['kind']
        safe_id(cid, 'navigation check id')

        check = by_check.get(cid)
        entries.append({'type': 'check', 'id': cid, 'kind': kind,
            'criteria': spec, 'execution': check.get('execution') if check else 'not_run',
            'outcome': check.get('outcome') if check else None,
            'native_coverage': {k: check.get('details', {}).get(k) for k in
                                ('sheet_instances', 'schematic_files')} if check and kind == 'netlist' else None,
            'evidence': check.get('evidence', []) if check else [],
            'capability': (baseline or {}).get('coverage', {}).get('checks', {}).get(cid),
            'source': {'path': 'candidate.json', 'scope_semantic_sha256': identity(manifest['scope']),
                       'locator': f'/scope/checks/{position}'}})
        # Only exact native output names and supported coordinator kinds may be parsed.
        filename = {'netlist': 'design.xml', 'geometry': 'geometry.json'}.get(kind)
        if not filename:
            continue
        rel = f'work/{cid}/{filename}'
        evidence = [e['path'] if isinstance(e, dict) else e for e in (check or {}).get('evidence', [])]
        if not check or check.get('execution') != 'ok' or rel not in evidence:
            diagnostics.append({'check': cid, 'reason': 'native inventory unavailable', 'path': rel})
            continue
        path = _source(run, rel)
        if not path.is_file():
            diagnostics.append({'check': cid, 'reason': 'missing native artifact', 'path': rel})
            continue
        sources[rel] = digest(path)
        source = {'path': rel}
        if path.stat().st_size > MAX_SOURCE_BYTES:
            diagnostics.append({'source': source, 'reason': 'native source byte limit exceeded'})
            continue
        try:
            data = path.read_bytes()
            if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
                raise ValueError('XML declarations are unsupported')
            if kind == 'netlist':
                root = ET.fromstring(data)
                if sum(1 for _ in root.iter()) > 100_000:
                    raise ValueError('native XML element limit exceeded')
                semantic = [ET.tostring(root.find(k), encoding='unicode') if root.find(k) is not None else None
                            for k in ('components', 'libparts', 'nets')]
            else:
                report = json.loads(data)
                if not isinstance(report, dict):
                    raise ValueError('unsupported geometry envelope')
                semantic = {k: report.get(k) for k in ('footprints', 'pads', 'tracks', 'zones', 'nets')}
            source['semantic_sha256'] = identity(semantic)
            source['format'] = 'kicad-native-xml' if kind == 'netlist' else 'coordinator-geometry-json'
            source['native_schema_version'] = root.get('version') if kind == 'netlist' else None
            source['extractor_schema'] = 'agent-ee.packet-index/1'
            found, edges, missing = (_netlist if kind == 'netlist' else _geometry)(data, source)
            if len(found) + len(entities) > MAX_ENTITIES:
                raise ValueError('native entity limit exceeded')
        except (ET.ParseError, ValueError, KeyError, TypeError) as exc:
            diagnostics.append({'source': source, 'reason': f'navigation extraction unavailable: {exc}'})
            continue
        diagnostics.extend(missing)
        for entity in found:
            entity.update(type='entity', checks=[cid], mappings=[{'check': cid,
                'source': {'path': rel, 'locator': entity['source']['locator']}, 'relation': 'native_inventory'}])
            entity['id'] = identity({'candidate_id': manifest['candidate_id'], 'kind': entity['kind'],
                'native_identity': entity['native_identity'], 'source_locator': entity['source']['locator'],
                'source_path': rel})
        entities.extend(found)
        connections.extend({'type': 'connection', 'relation': relation, 'from': a['id'], 'to': b['id'], 'source': s}
                           for a, b, s, relation in edges)
    specs = manifest['scope'].get('checks', [])
    for position, spec in enumerate(specs):
        params, cid = spec.get('params', {}), spec['id']
        result = by_check.get(cid, {})
        for entity in entities:
            native_path = entity['source']['path']
            ref = entity.get('reference', entity['label'])
            mapped, locator, relation = False, None, None
            if native_path == f"work/{params.get('netlist_check')}/design.xml":
                if spec['kind'] == 'pin_map':
                    symbols = params.get('symbols', [])
                    for j, symbol in enumerate(symbols):
                        if ref == symbol['reference'] and entity['kind'] == 'component':
                            mapped, locator = True, f'/scope/checks/{position}/params/symbols/{j}'
                            relation = 'declared_symbol_applicability'
                    if entity['kind'] == 'pin':
                        for j, comparison in enumerate(result.get('details', {}).get('comparisons', [])):
                            if ref == comparison['reference'] and entity['pin'] == comparison['pin']:
                                mapped, locator = True, f'/details/comparisons/{j}'
                                relation = 'retained_pin_comparison'
                if spec['kind'] == 'rc_transient':
                    elements = result.get('details', {}).get('elements', {})
                    if entity['kind'] in ('component', 'pin') and ref in elements:
                        mapped, locator = True, '/details/elements/' + ref
                        relation = 'retained_passive_element'
                    if entity['kind'] == 'net' and entity['label'] in [params.get(k) for k in ('input_net', 'output_net', 'ground_net')]:
                        mapped, locator = True, f'/scope/checks/{position}/params'
                        relation = 'declared_terminal'
                if spec['kind'] == 'active_spice':
                    binding = result.get('details', {}).get('binding', {})
                    if entity['kind'] in ('component', 'pin') and ref in binding.get('components', {}):
                        mapped, locator = True, '/details/binding/components/' + ref
                        relation = 'retained_model_binding'
            if spec['kind'] == 'kicad_happy' and entity['kind'] in ('component', 'pin'):
                # Explicit rule targets are applicability declarations, not analyzer conclusions.
                for j, rule in enumerate(params.get('rules', [])):
                    if ref in rule.get('components', []):
                        mapped, locator = True, f'/scope/checks/{position}/params/rules/{j}/components'
                        relation = 'declared_analyzer_target'
            if mapped and entity['identity_status'] != 'unresolved':
                entity['checks'].append(cid)
                entity['mappings'].append({'check': cid, 'relation': relation,
                    'source': {'path': f'checks/{cid}.json' if locator.startswith('/details') else 'candidate.json',
                               'locator': locator}})
    for req in (baseline or {}).get('requirements', []):
        entries.append({'type': 'requirement', **req})
    for spec in specs:
        if spec['kind'] != 'coupled_margins':
            continue
        params = spec.get('params', {})
        for member in params.get('members', []):
            result = by_check.get(member['check'], {})
            metric = result.get('details', {}).get('metrics', {}).get(member['metric'])
            linked = [e['id'] for e in entities if member['check'] in e['checks']]
            entries.append({'type': 'coupling', 'check': spec['id'], 'member': member,
                'condition': params.get('condition'), 'limits': params.get('limits'),
                'metric': metric, 'domain': result.get('details', {}).get('producer', {}).get('domain'),
                'entities': linked, 'entity_scope': 'mapped' if linked else 'unresolved',
                'source': {'path': f"checks/{member['check']}.json",
                           'locator': '/details/metrics/' + member['metric'].replace('~', '~0').replace('/', '~1')}})
    for entity in entities:
        entity['checks'] = sorted(set(entity['checks']))
        entity['requirements'] = sorted(r['id'] for r in (baseline or {}).get('requirements', [])
                                        if set(r['checks']).intersection(entity['checks']))
        entity['couplings'] = sorted({s['id'] for s in specs if s['kind'] == 'coupled_margins'
            and any(m['check'] in entity['checks'] for m in s.get('params', {}).get('members', []))})
    entries.extend(entities)
    entries.extend(connections)
    entries.sort(key=lambda e: (e['type'], e.get('kind', ''),
                                canonical(e.get('native_identity', e.get('id', ''))), canonical(e)))
    index = {'schema': 'agent-ee.packet-index/1', 'candidate_id': manifest['candidate_id'],
        'baseline_id': (baseline or {}).get('baseline_id'), 'entries': entries,
        'coverage': {'status': 'partial' if entities else 'unavailable', 'requirements': len((baseline or {}).get('requirements', [])),
            'checks': len(specs), 'observed_entities': len(entities),
            'entities_without_consumer_mapping': sum(len(e['checks']) == 1 for e in entities),
            'fallback_entities': sum(e['identity_status'] == 'fallback' for e in entities),
            'unresolved_entities': sum(e['identity_status'] == 'unresolved' for e in entities),
            'checks_without_entity_mapping': sorted(s['id'] for s in specs if not any(s['id'] in e['checks'] for e in entities)),
            'limitations': ['Supported native inventory only; no hierarchy completeness, physical equivalence or acceptance inferred.'],
            'diagnostics': diagnostics}}
    index['index_id'] = identity(index)
    binding = {'schema': 'agent-ee.packet-index-binding/1', 'index_id': index['index_id'],
        'candidate_id': manifest['candidate_id'], 'baseline_id': index['baseline_id'],
        'run_id': run_id, 'sources': sources,
        'source_hash_kind': 'raw-file-sha256',
        'access': {'candidate': '/candidate/<path>', 'evidence': '/evidence/<path>'},
        'check_projection_sha256': {c['id']: identity(c) for c in checks}}
    return {'index': index, 'binding': binding}


def page(index, *, byte_budget=12000, continuation=None):
    """Bound JSON UTF-8 display bytes; continuation addresses the full index."""
    if type(byte_budget) is not int or byte_budget < 1024:
        raise ValueError('navigation byte budget must be at least 1024')
    start = 0
    if continuation is not None:
        if not isinstance(continuation, dict) or set(continuation) != {'index_id', 'candidate_id', 'offset'}:
            raise ValueError('invalid navigation continuation')
        if (continuation.get('index_id') != index['index_id'] or
                continuation.get('candidate_id') != index['candidate_id']):
            raise ValueError('stale navigation continuation')
        start = continuation['offset']
    if type(start) is not int or not 0 <= start <= len(index['entries']):
        raise ValueError('invalid navigation offset')
    def view(end):
        return {'schema': 'agent-ee.packet-index-view/1', 'index_id': index['index_id'],
            'candidate_id': index['candidate_id'], 'total': len(index['entries']), 'offset': start, 'included': end - start,
            'entries': index['entries'][start:end],
            'coverage': {k: index['coverage'][k] for k in ('status', 'requirements', 'checks', 'observed_entities',
                'unresolved_entities', 'entities_without_consumer_mapping')},
            'diagnostics': {'count': len(index['coverage']['diagnostics']), 'index_pointer': '/coverage/diagnostics'},
            'access': '/evidence/work/.navigation/index.json',
            'continuation': ({'index_id': index['index_id'], 'candidate_id': index['candidate_id'],
                              'offset': end} if end < len(index['entries']) else None)}
    end = start
    while end < len(index['entries']) and len(json.dumps(view(end + 1)).encode('utf-8')) <= byte_budget:
        end += 1
    result = view(end)
    if end == start and end < len(index['entries']):
        # Oversized entries remain directly reachable; advance explicitly rather than loop.
        result['oversized_entry'] = {'index_pointer': f'/entries/{start}', 'bytes': len(canonical(index['entries'][start]))}
        result['continuation']['offset'] = start + 1
    return result


def retain(run, manifest, baseline, checks, run_id):
    built = build_index(run, manifest, baseline, checks, run_id)
    root = Path(run) / 'work/.navigation'
    if root.exists():
        verify_navigation(run, manifest, baseline, checks, run_id)
        return page(built['index'])
    write_json(root / 'index.json', built['index'])
    write_json(root / 'binding.json', built['binding'])
    return page(built['index'])


def verify_navigation(run, manifest, baseline, checks, run_id):
    root = Path(run) / 'work/.navigation'
    packet_path = Path(run) / 'packet.json'
    packet = read_json(packet_path) if packet_path.exists() else {}
    if not root.exists():
        if 'navigation' in packet:
            raise ValueError('packet navigation unavailable')
        return  # Historical packets have no navigation extension.
    _source(Path(run), 'work/.navigation/index.json')
    _source(Path(run), 'work/.navigation/binding.json')
    expected = build_index(run, manifest, baseline, checks, run_id)
    if read_json(root / 'index.json') != expected['index'] or read_json(root / 'binding.json') != expected['binding']:
        raise ValueError('packet navigation identity/binding changed')
    if packet and packet.get('navigation') != page(expected['index']):
        raise ValueError('packet navigation display differs from index')
