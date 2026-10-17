"""Bounded offline single-sheet extraction. Reports are diagnostic, not witnesses.

Only captured bytes are read by decompile(); the module CLI writes a new output
folder. Native qualification and emission remain separate tool operations.
"""
import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from uuid import UUID

from .connectivity_ir import INTENT_SCHEMA, canonical_resolved, resolve_intent, _validate_flat_intent
from .symbol_library import (children, field, nanometres, parse_library,
                             parse_sexpr, resolve_symbol)

ALGORITHM = 'agent-ee.schematic-decompile/1'
MAX_SYMBOLS = 128
MAX_PINS = 2048
MAX_WIRES = 2048
MAX_WORK = 2_000_000
MAX_POINTS = 8192
MAX_LIBRARY_BYTES = 32 * 1024 * 1024
ROOT_FIELDS = frozenset(('version', 'generator', 'generator_version', 'uuid', 'paper',
    'title_block', 'lib_symbols', 'symbol', 'wire', 'junction', 'global_label',
    'text', 'text_box', 'polyline', 'rectangle', 'circle', 'arc', 'image',
    'embedded_fonts'))
SYMBOL_FIELDS = frozenset(('lib_id', 'at', 'unit', 'in_bom', 'on_board', 'dnp',
    'uuid', 'property', 'instances', 'pin', 'mirror', 'fields_autoplaced'))


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


class Refusal(ValueError):
    def __init__(self, location, message):
        super().__init__(message)
        self.location = location


def _require(condition, location, message):
    if not condition:
        raise Refusal(location, message)


def _allowed(node, allowed, location):
    for index, child in enumerate(node[1:]):
        _require(isinstance(child, list) and child and child[0] in allowed,
                 location + '/' + str(index), 'unsupported or malformed child construct: ' + repr(child[:2]))


def _position(node, key='at', angle=False):
    values = field(node, key, 3 if angle else 2)
    xy = tuple(nanometres(x) for x in values[:2])
    _require(all(x % 100 == 0 and abs(x) <= 10**10 for x in xy), '/'+key,
             'coordinates must fit the qualified native 100 nm lattice and 10 m bound')
    if not angle:
        return xy
    rotation = nanometres(values[2])
    _require(rotation in (0, 90_000_000, 180_000_000, 270_000_000), '/at',
             'only 0/90/180/270 degree rotations are qualified')
    return xy, rotation // 1_000_000


def _pin_position(pin, anchor, rotation, mirror):
    # Library Y points up. KiCad instance rotations are counterclockwise on paper.
    x, y = pin['x_nm'], -pin['y_nm']
    x, y = {0: (x, y), 90: (y, -x), 180: (-x, -y), 270: (-y, x)}[rotation]
    if mirror == 'x':
        y = -y
    elif mirror == 'y':
        x = -x
    return anchor[0] + x, anchor[1] + y


def _pins(symbols, name, path, digest):
    pins, sources, power = resolve_symbol(symbols, name, path, digest)
    _require(not power, '/lib_symbols/' + name, 'power symbols unsupported')
    base = symbols[sources[0]['symbol']]
    _require(all(p['unit'] in (0, 1) for p in pins), '/lib_symbols/' + name, 'multiunit unsupported')
    for item in sources:
        definition = symbols[item['symbol']]
        _allowed([definition[0], *definition[2:]],
            {'symbol', 'extends', 'power', 'pin_names', 'pin_numbers', 'in_bom',
             'on_board', 'property', 'description', 'keywords', 'footprint_filters',
             'exclude_from_sim', 'embedded_fonts'}, '/lib_symbols/' + name)
        if children(definition, 'exclude_from_sim'):
            _require(field(definition, 'exclude_from_sim', 1) == ['no'],
                     '/lib_symbols/' + name, 'simulation exclusions unsupported')
    for unit in children(base, 'symbol'):
        _require(int(unit[1].rsplit('_', 2)[1]) in (0, 1), '/lib_symbols/' + name,
                 'multiunit definition unsupported')
        _allowed([unit[0], *unit[2:]],
            {'pin', 'polyline', 'rectangle', 'circle', 'arc', 'text', 'text_box', 'bezier'},
            '/lib_symbols/' + name)
        for pin in children(unit, 'pin'):
            _allowed([pin[0], *pin[3:]], {'at', 'length', 'name', 'number', 'hide'},
                     '/lib_symbols/' + name + '/pin')
            _require('hide' not in pin and not children(pin, 'hide'), '/lib_symbols/' + name,
                     'hidden pins unsupported')
            _require(len(pin) >= 3 and pin[2] == 'line', '/lib_symbols/' + name,
                     'alternate pin graphical style unsupported')
    _require(not any(p['electrical_type'] == 'no_connect' for p in pins),
             '/lib_symbols/' + name, 'no-connect pin type unsupported')
    _require(pins and len({p['number'] for p in pins}) == len(pins), '/lib_symbols/' + name,
             'empty or duplicate physical pin inventory')
    _require(all(p[k] % 100 == 0 and abs(p[k]) <= 10**10 for p in pins
                 for k in ('x_nm', 'y_nm')), '/lib_symbols/' + name,
             'library pins outside qualified native lattice/range')
    return pins


def _pin_signature(pins):
    return sorted(tuple(p[k] for k in ('number', 'name', 'electrical_type', 'unit',
        'x_nm', 'y_nm', 'orientation')) for p in pins)


def _components(root, declarations, captures):
    # Reuse the intent schema's manifest/path validation before any library reads.
    _validate_flat_intent(dict(schema=INTENT_SCHEMA, libraries=declarations,
        components=[], nets=[], no_connects=[]))
    libraries = {}
    total_bytes = 0
    _require(len(declarations) <= MAX_SYMBOLS, '/libraries', 'library count limit')
    for declaration in declarations:
        name, path, digest = (declaration[k] for k in ('name', 'path', 'sha256'))
        _require(name not in libraries and path not in [v[1] for v in libraries.values()],
                 '/libraries', 'duplicate library name or path')
        raw = captures.get(path)
        _require(isinstance(raw, bytes) and _hash(raw) == digest, '/libraries/' + path,
                 'missing or changed captured library')
        total_bytes += len(raw)
        _require(total_bytes <= MAX_LIBRARY_BYTES, '/libraries', 'aggregate captured library byte limit')
        libraries[name] = (parse_library(raw), path, digest)
    embedded_nodes = children(root, 'lib_symbols')
    _require(len(embedded_nodes) == 1, '/lib_symbols', 'expected embedded symbols')
    embedded = {}
    for node in embedded_nodes[0][1:]:
        _require(isinstance(node, list) and len(node) > 1 and node[0] == 'symbol'
                 and isinstance(node[1], str) and node[1] not in embedded,
                 '/lib_symbols', 'invalid or duplicate embedded symbol')
        embedded[node[1]] = node
    instances = children(root, 'symbol')
    _require(0 < len(instances) <= MAX_SYMBOLS, '/symbol', 'symbol count limit or empty design')
    components, terminals, anchors, seen_refs, seen_ids, used = [], {}, {}, set(), set(), set()
    root_id = field(root, 'uuid', 1)[0]
    for index, node in enumerate(instances):
        loc = f'/symbol/{index}'
        _allowed(node, SYMBOL_FIELDS, loc)
        lib_id = field(node, 'lib_id', 1)[0]
        _require(lib_id.count(':') == 1, loc, 'invalid lib_id')
        library, name = lib_id.split(':')
        _require(library in libraries and lib_id in embedded, loc, 'missing captured/embedded symbol')
        symbols, path, digest = libraries[library]
        used.add(library)
        pins = _pins(symbols, name, path, digest)
        actual = _pins(embedded, lib_id, 'embedded', _hash(b''))
        _require(_pin_signature(pins) == _pin_signature(actual), loc,
                 'embedded symbol differs from captured library')
        _require(field(node, 'unit', 1) == ['1'], loc, 'multiunit instance unsupported')
        instance_pins = set()
        for pin in children(node, 'pin'):
            _require(len(pin) >= 2 and pin[1] in {p['number'] for p in pins}
                     and pin[1] not in instance_pins, loc, 'invalid instance pin inventory')
            instance_pins.add(pin[1])
            _allowed([pin[0], *pin[2:]], {'uuid'}, loc + '/pin')
            field(pin, 'uuid', 1)
        for key in ('in_bom', 'on_board'):
            _require(field(node, key, 1) == ['yes'], loc, key + ' exclusion unsupported')
        if children(node, 'dnp'):
            _require(field(node, 'dnp', 1) == ['no'], loc, 'DNP unsupported')
        props = {}
        for prop in children(node, 'property'):
            _require(len(prop) >= 3 and isinstance(prop[1], str) and isinstance(prop[2], str)
                     and prop[1] not in props, loc, 'duplicate/malformed property')
            _require(prop[1] in ('Reference', 'Value', 'Footprint', 'Datasheet', 'Description'),
                     loc, 'unrepresented instance property')
            props[prop[1]] = prop[2]
        _require('Reference' in props and 'Value' in props and props['Reference'], loc,
                 'missing reference/value')
        _require(all('${' not in value for value in props.values()), loc,
                 'unqualified text variable substitution')
        cid = field(node, 'uuid', 1)[0]
        _require(cid and cid not in seen_ids and props['Reference'] not in seen_refs, loc,
                 'duplicate or empty component identity')
        seen_ids.add(cid)
        seen_refs.add(props['Reference'])
        # Stored instance paths must not disagree with the actual single-sheet instance.
        records = children(node, 'instances')
        _require(len(records) == 1, loc, 'missing/duplicate instance metadata')
        _allowed(records[0], {'project'}, loc + '/instances')
        projects = children(records[0], 'project')
        _require(len(projects) == 1, loc, 'multiple project instances unsupported')
        _allowed([projects[0][0], *projects[0][2:]], {'path'}, loc + '/instances/project')
        paths = children(projects[0], 'path')
        _require(len(paths) == 1 and len(paths[0]) > 1 and paths[0][1] == '/' + root_id
            and field(paths[0], 'reference', 1) == [props['Reference']]
            and field(paths[0], 'unit', 1) == ['1'], loc, 'inconsistent instance path')
        _allowed([paths[0][0], *paths[0][2:]], {'reference', 'unit'}, loc + '/instances/path')
        anchor, rotation = _position(node, angle=True)
        mirror = field(node, 'mirror', 1)[0] if children(node, 'mirror') else ''
        _require(mirror in ('', 'x', 'y'), loc, 'unsupported mirror')
        comp = dict(id=cid, reference=props['Reference'], lib_id=lib_id,
                    value=props['Value'], footprint=props.get('Footprint', ''), units=[1])
        components.append(comp)
        anchors[cid] = dict(sheet='/', uuid=cid)
        for pin in pins:
            terminals[cid, pin['number']] = _pin_position(pin, anchor, rotation, mirror)
        _require(len(terminals) <= MAX_PINS, loc, 'physical pin count limit')
    _require(used == set(libraries), '/libraries', 'unused library declarations unsupported')
    return sorted(components, key=lambda c: c['id']), terminals, anchors


def _on_segment(point, a, b):
    x, y = point
    return ((b[0]-a[0])*(y-a[1]) == (b[1]-a[1])*(x-a[0])
            and min(a[0], b[0]) <= x <= max(a[0], b[0])
            and min(a[1], b[1]) <= y <= max(a[1], b[1]))


class _Graph:
    def __init__(self, points):
        self.parents = {p: p for p in points}

    def find(self, point):
        while self.parents[point] != point:
            self.parents[point] = self.parents[self.parents[point]]
            point = self.parents[point]
        return point

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parents[max(a, b)] = min(a, b)


def _global_label_name(name, location):
    # Qualified against KiCad 10.0.7 original-native exports. In particular,
    # {brace} can expose another escape; do not guess recursive decoding order.
    escapes = {'{slash}': '/', '{backslash}': '\\', '{colon}': ':',
               '{space}': ' ', '{quote}': "'"}
    _require('${' not in name, location, 'text variables unsupported')
    _require(not any(ord(c) < 32 or ord(c) == 127 for c in name), location,
             'unqualified control character in global label')
    if re.fullmatch(r'~\{[A-Z0-9_]+\}', name):
        return name
    _require('~' not in name, location, 'unqualified global label overbar semantics')
    pattern = r'\{(?:slash|backslash|colon|space|quote)\}'
    remainder = re.sub(pattern, '', name)
    _require('{' not in remainder and '}' not in remainder, location,
             'unqualified global label brace/encoding semantics')
    return re.sub(pattern, lambda match: escapes.get(match[0], match[0]), name)


def _nets(root, terminals):
    wires = children(root, 'wire')
    _require(len(wires) <= MAX_WIRES, '/wire', 'wire count limit')
    segments = []
    for index, wire in enumerate(wires):
        _allowed(wire, {'pts', 'stroke', 'uuid'}, f'/wire/{index}')
        field(wire, 'uuid', 1)
        pts = children(wire, 'pts')
        _require(len(pts) == 1 and len(pts[0]) == 3
                 and len(children(pts[0], 'xy')) == 2, f'/wire/{index}', 'wire needs two endpoints')
        segments.append(tuple(_position(['point', p], key='xy') for p in pts[0][1:]))
    junctions = set()
    for index, junction in enumerate(children(root, 'junction')):
        _allowed(junction, {'at', 'diameter', 'color', 'uuid'}, f'/junction/{index}')
        field(junction, 'uuid', 1)
        junctions.add(_position(junction))
    labels = []
    label_names = {}
    for index, label in enumerate(children(root, 'global_label')):
        _allowed([label[0], *label[2:]], {'shape', 'at', 'effects', 'uuid', 'property', 'fields_autoplaced'},
                 f'/global_label/{index}')
        _require(len(label) > 1 and isinstance(label[1], str) and label[1],
                 f'/global_label/{index}', 'empty global label')
        field(label, 'uuid', 1)
        label_names[label[1]] = _global_label_name(label[1], f'/global_label/{index}')
        position, _ = _position(label, angle=True)
        labels.append((label[1], position))
    endpoints = {p for segment in segments for p in segment}
    points = set(terminals.values()) | endpoints | junctions | {p for _, p in labels}
    _require(len(points) <= MAX_POINTS, '/graph', 'coordinate node count limit')
    _require(len(points) * len(segments) <= MAX_WORK, '/wire', 'graph work limit')
    # A wire is its own conductor. KiCad does not split an unsplit segment
    # merely because another endpoint or pin lies on its interior. Only an
    # explicit junction or global label attaches to an interior in this subset.
    point_key = lambda p: ('point', *p)
    wire_keys = [('wire', i) for i in range(len(segments))]
    graph = _Graph([*(point_key(p) for p in points), *wire_keys])
    for key, (a, b) in zip(wire_keys, segments):
        graph.join(key, point_key(a))
        graph.join(key, point_key(b))
        for point in junctions | {p for _, p in labels}:
            if _on_segment(point, a, b):
                graph.join(key, point_key(point))
    names = {}
    for raw_name, point in sorted(labels):
        name = label_names[raw_name]
        if name in names:
            graph.join(point_key(names[name]), point_key(point))
        names[name] = point
    groups = {}
    for key in sorted(graph.parents):
        groups.setdefault(graph.find(key), dict(terminals=[], labels=[]))
    for terminal, point in sorted(terminals.items()):
        groups[graph.find(point_key(point))]['terminals'].append(terminal)
    for name, point in sorted(labels):
        groups[graph.find(point_key(point))]['labels'].append(dict(name=name, position_nm=list(point)))
    nets, reports = [], []
    for group in groups.values():
        physical = group['terminals']
        _require(len(physical) >= 2, '/graph/' + repr(physical),
                 'singleton/unaccounted pin or conductor without two physical terminals')
        native_names = {label_names[l['name']] for l in group['labels']}
        _require(len(native_names) <= 1, '/graph/' + repr(physical), 'conflicting global net names')
        nid = 'n-' + _hash(_bytes(physical))
        nets.append(dict(id=nid, name=next(iter(native_names), ''), terminals=[
            dict(component=c, pin={'number': p}) for c, p in physical]))
        # Canonical star: a deterministic spanning tree over distinct physical
        # terminals; geometry determines membership, never this presentation.
        edges = [dict(source=dict(component=physical[0][0], pin=physical[0][1]),
                      target=dict(component=c, pin=p)) for c, p in physical[1:]]
        reports.append(dict(id=nid, physical_terminal_count=len(physical),
            connect_pins=edges, global_labels=group['labels']))
    return sorted(nets, key=lambda n: n['id']), sorted(reports, key=lambda n: n['id'])


def decompile(source, libraries, captured_libraries):
    """Return all-or-nothing intent/resolution and a source-bound diagnostic report.

    No native execution is claimed. Unsupported inputs expose no partial IR.
    Libraries are the existing intent/1 name/path/sha256 manifests.
    """
    report = dict(schema=ALGORITHM, execution='unsupported', outcome='unknown', coverage='missing',
        source_sha256=_hash(source) if isinstance(source, bytes) else None,
        libraries=libraries, findings=[], nets=[], source_anchors={}, construct_inventory={},
        native_qualification='not_run', roundtrip='not_run', source_pins=[],
        ignored_non_electrical=[])
    result = dict(intent=None, resolved=None, report=report)
    try:
        _require(isinstance(source, bytes) and len(source) <= 8 * 1024 * 1024,
                 '/', 'source must be bytes at most 8 MiB')
        root = parse_sexpr(source)
        _require(root[0] == 'kicad_sch', '/', 'expected kicad_sch')
        report['construct_inventory'] = dict(sorted(Counter(
            str(x[0]) for x in root[1:] if isinstance(x, list) and x).items()))
        # Scan the whole root before extracting anything. No unsupported electrical
        # construct can be silently omitted from an otherwise usable intent.
        _allowed(root, ROOT_FIELDS, '/')
        pending, uuids = [root], set()
        while pending:
            node = pending.pop()
            if node[0] == 'uuid':
                _require(len(node) == 2 and isinstance(node[1], str) and
                         str(UUID(node[1])) == node[1] and node[1] not in uuids,
                         '/uuid', 'invalid/duplicate native UUID')
                uuids.add(node[1])
            pending.extend(x for x in node[1:] if isinstance(x, list) and x)
        _require(field(root, 'version', 1) == ['20250114'], '/version', 'unqualified schematic version')
        _require(isinstance(captured_libraries, dict), '/libraries', 'expected capture map')
        components, terminals, anchors = _components(root, libraries, captured_libraries)
        nets, net_reports = _nets(root, terminals)
        intent = dict(schema=INTENT_SCHEMA, libraries=sorted(libraries, key=lambda l: l['name']),
                      components=components, nets=nets, no_connects=[])
        resolved = resolve_intent(intent, captured_libraries)
        _require((resolved['execution'], resolved['outcome'], resolved['coverage']) == ('ok', 'pass', 'complete'),
                 '/resolved', 'existing resolver refused extracted intent: ' + repr(resolved['findings']))
        report.update(execution='ok', outcome='pass', coverage='complete', nets=net_reports,
                      source_anchors=anchors, source_pins=[dict(component=c, pin=p,
                          position_nm=list(position)) for (c, p), position in sorted(terminals.items())],
                      ignored_non_electrical=[dict(construct=k, count=v) for k,v in
                          report['construct_inventory'].items() if k in
                          {'text', 'text_box', 'polyline', 'rectangle', 'circle', 'arc', 'image', 'title_block'}],
                      intent_sha256=_hash(_bytes(intent)),
                      resolved_sha256=_hash(canonical_resolved(resolved)))
        result.update(intent=intent, resolved=resolved)
    except Refusal as exc:
        report['findings'].append(dict(code='unsupported', location=exc.location, message=str(exc)))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        report['findings'].append(dict(code='input_invalid', location='/', message=str(exc)))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('schematic', type=Path)
    parser.add_argument('--libraries', type=Path, required=True, help='intent/1 library manifest list JSON')
    parser.add_argument('--captures', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    with args.schematic.open('rb') as stream:
        source = stream.read(8 * 1024 * 1024 + 1)
    with args.libraries.open('rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    _require(len(raw) <= 1024 * 1024, '/libraries', 'manifest size limit')
    from .connectivity_ir import _read_json
    libraries = _read_json(raw)
    _validate_flat_intent(dict(schema=INTENT_SCHEMA, libraries=libraries, components=[], nets=[], no_connects=[]))
    _require(len(libraries) <= MAX_SYMBOLS, '/libraries', 'library count limit')
    captures = {}
    total_bytes = 0
    root = args.captures.resolve(strict=True)
    for lib in libraries:
        path = root / lib['path']
        _require(not any(p.is_symlink() for p in (path, *path.parents) if p != root.parent),
                 '/libraries', 'symlink capture unsupported')
        if path.is_file():
            with path.open('rb') as stream:
                captures[lib['path']] = stream.read(MAX_LIBRARY_BYTES - total_bytes + 1)
            total_bytes += len(captures[lib['path']])
            if total_bytes > MAX_LIBRARY_BYTES:
                break
    result = decompile(source, libraries, captures)
    args.out.mkdir(parents=True, exist_ok=False)
    for name in ('report', 'intent', 'resolved'):
        if result[name] is not None:
            (args.out / (name + '.json')).write_bytes(_bytes(result[name]) + b'\n')
    return 0 if result['report']['execution'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
