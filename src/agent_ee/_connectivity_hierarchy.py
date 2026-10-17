"""Bounded occurrence elaboration; library and pin resolution stays in the flat resolver."""
import copy
import hashlib
from pathlib import PurePosixPath

from . import connectivity_ir as ir

INTENT_SCHEMA = 'agent-ee.connectivity-intent/2'
RESOLVED_SCHEMA = 'agent-ee.resolved-connectivity/2'
MAX_COMPONENTS = 128
MAX_TERMINALS = 8192
DIRECTIONS = {'input', 'output', 'bidirectional', 'tri_state', 'passive'}
RESERVED_LABEL = '__agent_ee_'


class BoundaryDefect(ValueError):
    """Valid declarations would emit alias ports for one physical boundary."""


class Unsupported(ValueError):
    """A well-formed declaration exceeds the qualified hierarchy subset."""


def _bounded_list(value):
    if not isinstance(value, list):
        raise ValueError('expected list')
    if len(value) > 10000:
        raise Unsupported('declaration array exceeds 10000 entries')


def _identifier(value):
    ir._text(value)
    if len(value.encode('utf-8')) > 256:
        raise Unsupported('scoped identifier exceeds 256 UTF-8 bytes')


def scoped(sheet, local_id):
    return ir._json_bytes([sheet, local_id]).decode('utf-8')


def _tree(hierarchy):
    _bounded_list(hierarchy)
    if len(hierarchy) > 256:
        raise Unsupported('hierarchy exceeds 256 occurrences')
    sheets, siblings, files = {}, set(), set()
    for sheet in hierarchy:
        ir._object(sheet, ('id', 'parent', 'local_id', 'file'))
        for key in ('id', 'local_id'):
            _identifier(sheet[key])
        ir._text(sheet['file'])
        if sheet['parent'] is not None:
            _identifier(sheet['parent'])
        path = PurePosixPath(sheet['file'])
        if (path.is_absolute() or '..' in path.parts or str(path) != sheet['file']
                or '\\' in sheet['file'] or path.suffix != '.kicad_sch'):
            raise ValueError('unsafe schematic file: ' + sheet['file'])
        if sheet['id'] in sheets or (sheet['parent'], sheet['local_id']) in siblings:
            raise ValueError('duplicate occurrence or sibling local_id')
        sheets[sheet['id']] = sheet
        siblings.add((sheet['parent'], sheet['local_id']))
        if sheet['file'] in files:
            raise Unsupported('repeated sheet file requires native instance qualification')
        files.add(sheet['file'])
    if 'root' not in sheets or sheets['root']['parent'] is not None or sheets['root']['local_id'] != 'root':
        raise ValueError('required root/null/root occurrence missing')
    for sheet in hierarchy:
        seen, current = set(), sheet['id']
        while current != 'root':
            if current in seen or current not in sheets:
                raise ValueError('cyclic or disconnected hierarchy')
            seen.add(current)
            if len(seen) > 16:
                raise Unsupported('hierarchy exceeds depth 16')
            current = sheets[current]['parent']
        # A file cannot also serve as an ancestor directory of another file.
        if any(str(p) in files for p in PurePosixPath(sheet['file']).parents):
            raise ValueError('schematic file/directory collision')
    return sheets


def _label(net):
    return net['name'] or RESERVED_LABEL + hashlib.sha256(scoped(net['sheet'], net['id']).encode('utf-8')).hexdigest()


def validate_intent(raw):
    ir._object(raw, ('schema', 'libraries', 'components', 'nets', 'no_connects', 'hierarchy', 'ports'))
    if raw['schema'] != INTENT_SCHEMA:
        raise ValueError('unknown hierarchy schema')
    # Validate the old field vocabulary through the existing validator.
    flat = dict(schema=ir.INTENT_SCHEMA, libraries=raw['libraries'], components=[], nets=[], no_connects=[])
    for key in ('libraries', 'components', 'nets', 'no_connects', 'hierarchy', 'ports'):
        _bounded_list(raw[key])
    for kind in ('components', 'nets', 'no_connects'):
        for item in raw[kind]:
            if not isinstance(item, dict) or 'sheet' not in item:
                raise ValueError('scoped declaration requires sheet')
            _identifier(item['sheet'])
            _identifier(item['component'] if kind == 'no_connects' else item.get('id'))
            if kind == 'components':
                _bounded_list(item.get('units'))
            elif kind == 'nets':
                _bounded_list(item.get('terminals'))
            flat[kind].append({k: v for k, v in item.items() if k != 'sheet'})
    ir._validate_flat_intent(flat)
    if len(raw['components']) > MAX_COMPONENTS:
        raise Unsupported('component inventory exceeds 128')
    sheets = _tree(raw['hierarchy'])
    paths = {s['file'] for s in raw['hierarchy']} | {lib['path'] for lib in raw['libraries']}
    if any(str(parent) in paths for path in paths for parent in PurePosixPath(path).parents):
        raise ValueError('captured file/directory collision')
    components, segments, references, labels = set(), {}, set(), set()
    for comp in raw['components']:
        key = (comp['sheet'], comp['id'])
        if comp['sheet'] not in sheets or key in components or comp['reference'] in references:
            raise ValueError('unknown component scope or duplicate component/reference')
        components.add(key)
        references.add(comp['reference'])
    for net in raw['nets']:
        key = (net['sheet'], net['id'])
        label = _label(net)
        if net['name'].startswith(RESERVED_LABEL):
            raise ValueError('explicit net name uses reserved label prefix')
        if net['sheet'] not in sheets or key in segments or (net['sheet'], label) in labels:
            raise ValueError('unknown net scope or duplicate local net id/label')
        segments[key] = net
        labels.add((net['sheet'], label))
    for kind in ('nets', 'no_connects'):
        for item in raw[kind]:
            if item['sheet'] not in sheets:
                raise ValueError('unknown terminal scope')
            for term in item['terminals'] if kind == 'nets' else [item]:
                if (item['sheet'], term['component']) not in components:
                    raise ValueError('unknown local component; cross-sheet terminals require ports')
    ids, names = set(), set()
    for port in raw['ports']:
        ir._object(port, ('child', 'id', 'name', 'direction', 'parent_net', 'child_net'))
        for value in port.values():
            _identifier(value)
        child = sheets.get(port['child'])
        if child is None or child['parent'] is None or port['direction'] not in DIRECTIONS:
            raise ValueError('invalid child boundary or port direction')
        if (child['parent'], port['parent_net']) not in segments or (child['id'], port['child_net']) not in segments:
            raise ValueError('unknown port segment')
        if (child['id'], port['id']) in ids or (child['id'], port['name']) in names:
            raise ValueError('duplicate boundary port id/name')
        ids.add((child['id'], port['id']))
        names.add((child['id'], port['name']))
    return sheets


def _normal(raw):
    raw = copy.deepcopy(raw)
    for key in ('libraries', 'components', 'nets', 'no_connects', 'hierarchy', 'ports'):
        raw[key].sort(key=ir._json_bytes)
    for comp in raw['components']:
        comp['units'].sort()
    for net in raw['nets']:
        net['terminals'].sort(key=ir._json_bytes)
    return raw


def _classes(hierarchy, local_nets, ports):
    sheets = {s['id']: s for s in hierarchy}
    parent = {(n['sheet'], n['id']): (n['sheet'], n['id']) for n in local_nets}
    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    for port in ports:
        a = find((sheets[port['child']]['parent'], port['parent_net']))
        b = find((port['child'], port['child_net']))
        parent[max(a, b)] = min(a, b)
    groups = {}
    for key in parent:
        groups.setdefault(find(key), []).append(key)
    mapping = {}
    for keys in groups.values():
        identity = 'net:' + hashlib.sha256(ir._json_bytes([list(k) for k in sorted(keys)])).hexdigest()
        for key in keys:
            mapping[key] = identity
    boundaries = set()
    for port in ports:
        boundary = (port['child'], mapping[(port['child'], port['child_net'])])
        if boundary in boundaries:
            raise BoundaryDefect('multiple ports for one boundary physical net')
        boundaries.add(boundary)
    return mapping


def _unused_ports(record):
    sheets = {s['id']: s for s in record['hierarchy']}
    ancestors = {}
    for sheet in sheets:
        path, current = set(), sheet
        while current is not None:
            path.add(current)
            current = sheets[current]['parent']
        ancestors[sheet] = path
    counts = {}
    for net in record['local_nets']:
        counts.setdefault(net['physical_net'], {}).setdefault(net['sheet'], 0)
        counts[net['physical_net']][net['sheet']] += len(net['terminals'])
    mapping = {(n['sheet'], n['id']): n['physical_net'] for n in record['local_nets']}
    unused = []
    for port in record['ports']:
        by_sheet = counts[mapping[(port['child'], port['child_net'])]]
        inside = sum(n for sheet, n in by_sheet.items() if port['child'] in ancestors[sheet])
        if not inside or inside == sum(by_sheet.values()):
            unused.append(dict(code='unused_port', location='/ports/' + scoped(port['child'], port['id']),
                               message='boundary port must reach physical terminals on both sides'))
    return unused


def resolve(raw, captures):
    result = dict(schema=RESOLVED_SCHEMA, execution='ok', outcome='pass', coverage='complete',
                  findings=[], intent_sha256=None, libraries=[], components=[], nets=[],
                  no_connects=[], hierarchy=[], ports=[], local_nets=[])
    try:
        validate_intent(raw)
        raw = _normal(raw)
        mapping = _classes(raw['hierarchy'], raw['nets'], raw['ports'])
    except (ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, BoundaryDefect):
            result.update(execution='ok', outcome='defect', coverage='complete')
        else:
            result.update(execution='unsupported' if isinstance(exc, Unsupported) else 'malformed',
                          outcome='unknown', coverage='missing')
        result['findings'] = [dict(code='hierarchy_schema', location='/', message=str(exc))]
        return ir._seal(result)
    flat = dict(schema=ir.INTENT_SCHEMA, libraries=raw['libraries'], components=[], nets=[], no_connects=[])
    for comp in raw['components']:
        flat['components'].append(dict({k: v for k, v in comp.items() if k != 'sheet'}, id=scoped(comp['sheet'], comp['id'])))
    def terminal(term, sheet):
        return dict(component=scoped(sheet, term['component']), pin=term['pin'])
    for net in raw['nets']:
        flat['nets'].append(dict(id=scoped(net['sheet'], net['id']), name=net['name'],
                                 terminals=[terminal(t, net['sheet']) for t in net['terminals']]))
    flat['no_connects'] = [terminal(t, t['sheet']) for t in raw['no_connects']]
    if len(ir._json_bytes(flat)) > 8 * 1024 * 1024:
        result.update(execution='unsupported', outcome='unknown', coverage='missing')
        result['findings'] = [dict(code='projection_limit', location='/', message='scoped projection exceeds 8 MiB')]
        return ir._seal(result)
    physical = ir._resolve_flat_intent(flat, captures, _pin_limit=MAX_TERMINALS)
    result.update({k: physical[k] for k in ('execution', 'coverage', 'libraries', 'components', 'no_connects')})
    result['findings'] = [f for f in physical['findings'] if f['code'] != 'singleton_net']
    result['intent_sha256'] = hashlib.sha256(ir._json_bytes(raw)).hexdigest()
    result['hierarchy'], result['ports'] = raw['hierarchy'], raw['ports']
    comp_sources = {scoped(c['sheet'], c['id']): c for c in raw['components']}
    for comp in result['components']:
        source = comp_sources[comp['id']]
        comp.update(sheet=source['sheet'], local_id=source['id'])
    resolved_segments = {n['id']: n for n in physical['nets']}
    nets = {}
    for source in raw['nets']:
        identity = mapping[(source['sheet'], source['id'])]
        # A failed capture-map boundary returns before resolving any segments.
        terms = resolved_segments.get(scoped(source['sheet'], source['id']), {'terminals': []})['terminals']
        result['local_nets'].append(dict(source, terminals=terms, label=_label(source), physical_net=identity))
        nets.setdefault(identity, []).extend(terms)
    for identity, terms in sorted(nets.items()):
        result['nets'].append(dict(id=identity, name='', terminals=sorted(terms, key=ir._json_bytes)))
        if len({(t['component'], t['pin']) for t in terms}) < 2:
            result['findings'].append(dict(code='singleton_net', location='/nets/' + identity,
                                           message='physical net needs two distinct physical terminals'))
    if len(raw['hierarchy']) > 1 and any(c['power'] for c in result['components']):
        if result['execution'] != 'failed':
            result.update(execution='unsupported', coverage='missing')
        result['findings'].append(dict(code='hierarchical_power_unsupported', location='/components',
                                       message='native global power equivalence is not qualified'))
    if sum(len(c['pins']) for c in result['components']) > MAX_TERMINALS:
        if result['execution'] != 'failed':
            result.update(execution='unsupported', coverage='missing')
        result['findings'].append(dict(code='terminal_limit', location='/components',
                                       message='physical pin inventory exceeds 8192'))
    result['findings'].extend(_unused_ports(result))
    result['outcome'] = ('unknown' if result['execution'] != 'ok' else
                         'defect' if result['findings'] else 'pass')
    result['findings'].sort(key=ir._json_bytes)
    return ir._seal(result)


def validate_resolved(record):
    ir._object(record, ('schema', 'execution', 'outcome', 'coverage', 'findings', 'intent_sha256',
                       'libraries', 'components', 'nets', 'no_connects', 'hierarchy', 'ports',
                       'local_nets', 'canonical_sha256'))
    if (record['execution'], record['outcome'], record['coverage']) != ('ok', 'pass', 'complete') or record['findings']:
        raise ValueError('resolved intent is not complete and passing')
    payload = {k: v for k, v in record.items() if k != 'canonical_sha256'}
    if hashlib.sha256(ir._json_bytes(payload)).hexdigest() != record['canonical_sha256']:
        raise ValueError('resolved identity mismatch')
    # Reconstruct the declaration boundary and recheck every scoped association.
    raw = dict(schema=INTENT_SCHEMA, libraries=record['libraries'], components=[], nets=[],
               no_connects=[], hierarchy=record['hierarchy'], ports=record['ports'])
    for key in ('libraries', 'components', 'nets', 'no_connects', 'hierarchy', 'ports', 'local_nets'):
        _bounded_list(record[key])
    ids = {}
    for comp in record['components']:
        ir._object(comp, ('id', 'reference', 'lib_id', 'value', 'footprint', 'units', 'pins', 'sources',
                          'power', 'field_sources', 'sheet', 'local_id'), ('part_binding',))
        for key in ('id', 'sheet', 'local_id'):
            ir._text(comp[key])
        if comp['id'] != scoped(comp['sheet'], comp['local_id']):
            raise ValueError('noninjective scoped component identity')
        ids[comp['id']] = comp
        raw['components'].append(dict({k: comp[k] for k in ('reference', 'lib_id', 'value', 'footprint', 'units')},
                                      id=comp['local_id'], sheet=comp['sheet']))
    def terminal(term, sheet):
        ir._object(term, ('component', 'pin'))
        ir._text(term['component'])
        ir._text(term['pin'])
        comp = ids.get(term['component'])
        if comp is None or comp['sheet'] != sheet:
            raise ValueError('local segment crosses component scope')
        return dict(component=comp['local_id'], pin={'number': term['pin']})
    _bounded_list(record['local_nets'])
    for net in record['local_nets']:
        ir._object(net, ('sheet', 'id', 'name', 'label', 'terminals', 'physical_net'))
        _bounded_list(net['terminals'])
        if net['label'] != _label(net):
            raise ValueError('local label identity mismatch')
        raw['nets'].append(dict(sheet=net['sheet'], id=net['id'], name=net['name'],
                                terminals=[terminal(t, net['sheet']) for t in net['terminals']]))
    for term in record['no_connects']:
        comp = ids.get(term.get('component')) if isinstance(term, dict) else None
        if comp is None:
            raise ValueError('unknown no-connect component')
        raw['no_connects'].append(dict(terminal(term, comp['sheet']), sheet=comp['sheet']))
    validate_intent(raw)
    mapping = _classes(record['hierarchy'], record['local_nets'], record['ports'])
    expected = {}
    for net in record['local_nets']:
        identity = mapping[(net['sheet'], net['id'])]
        if identity != net['physical_net']:
            raise ValueError('port equivalence mismatch')
        expected.setdefault(identity, []).extend(net['terminals'])
    expected = [dict(id=k, name='', terminals=sorted(v, key=ir._json_bytes)) for k, v in sorted(expected.items())]
    if record['nets'] != expected:
        raise ValueError('physical net projection mismatch')
    if _unused_ports(record):
        raise ValueError('unused boundary port')
    if sum(len(c['pins']) for c in record['components']) > MAX_TERMINALS:
        raise Unsupported('terminal inventory exceeds 8192')
    if len(raw['hierarchy']) > 1 and any(c['power'] for c in record['components']):
        raise ValueError('unqualified hierarchical power')
    flat = {k: copy.deepcopy(v) for k, v in record.items() if k != 'local_nets'}
    flat.update(schema=ir.RESOLVED_SCHEMA, hierarchy=[], ports=[])
    for comp in flat['components']:
        del comp['sheet'], comp['local_id']
    flat.pop('canonical_sha256')
    ir._validate_flat_resolved(ir._seal(flat))
    return record
