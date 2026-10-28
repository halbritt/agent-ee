"""Explicit selected-reference composition over captured flat source instances.

Uses the shared native reader, symbol resolver, geometry and S-expression writer.
This establishes provenance only; it cannot qualify an unverified source block.
"""
import copy
import tempfile
import uuid
from pathlib import Path

from .hardware_interface import exact_keys, text, sheet_items, marker_isolation, xy
from .reference_blocks import sheet_symbols
from .schematic_emit import Quoted, format_sexpr, parse_quoted, _mm
from .schematic_lint import _Symbol
from .storage import digest
from .symbol_library import children, field, parse_sexpr


def validate(plan, block):
    exact_keys(plan, 'schema sources ports reason')
    if plan['schema'] != 'agent-ee.block-composition/1':
        raise ValueError('unknown block composition schema')
    text(plan['reason'])
    if not isinstance(plan['sources'], list) or len(plan['sources']) != len(block.get('instances', [])):
        raise ValueError('composition must cover every captured source instance')
    refs, seen = set(), set()
    for source in plan['sources']:
        exact_keys(source, 'sheet uuid_path sha256 references nets reason')
        text(source['reason'])
        matches = [i for i in block['instances'] if (i['sheet'], i['uuid_path']) ==
                   (source['sheet'], source['uuid_path'])]
        identity = (source['sheet'], source['uuid_path'])
        if len(matches) != 1 or identity in seen or source['sha256'] != block['files'][source['sheet']]:
            raise ValueError('composition source identity missing, duplicated or changed')
        seen.add(identity)
        mapping = source['references']
        if not isinstance(mapping, dict) or set(mapping) != set(matches[0]['references']):
            raise ValueError('composition references must cover the selected source references exactly')
        for ref in mapping.values():
            text(ref)
            if ref.startswith('#') or ref in refs:
                raise ValueError('duplicate or virtual delivered reference')
            refs.add(ref)
        nets = source['nets']
        if not isinstance(nets, dict) or not nets or len(set(nets.values())) != len(nets):
            raise ValueError('source nets require explicit mappings without undeclared aliasing')
        for a, b in nets.items():
            text(a); text(b)
    if not isinstance(plan['ports'], list) or not plan['ports']:
        raise ValueError('composition requires explicit ports')
    ports = {}
    for port in plan['ports']:
        exact_keys(port, 'name net_name direction voltage_domain current_a alias_of reason')
        for k in ('name', 'net_name', 'voltage_domain', 'reason'):
            text(port[k])
        if port['name'] in ports or port['direction'] not in ('input', 'output', 'bidirectional', 'tri_state', 'passive'):
            raise ValueError('duplicate or unsupported composition port')
        # Current limits are not established by connectivity preservation.
        if port['current_a'] is not None:
            raise ValueError('composition does not establish port current limits')
        ports[port['name']] = port
    canonical = {name for name, port in ports.items() if port['alias_of'] is None}
    for name, port in ports.items():
        target = port['alias_of'] or name
        if target not in canonical or port['net_name'] != target:
            raise ValueError('port alias must explicitly name one canonical net')
    if {n for s in plan['sources'] for n in s['nets'].values()} != canonical:
        raise ValueError('mapped source nets and canonical ports differ')
    return [{k: v for k, v in port.items() if k not in ('alias_of', 'reason')} for port in plan['ports']]


def _no_connect_status(tree, graph, reference, pin):
    """Prove a physical NC from captured geometry and the independent export."""
    comp = graph['components'].get(reference)
    if comp is None or pin not in comp['pins']:
        return 'violation', 'physical no-connect pin is missing'
    net = graph['nets'].get(graph['memberships'].get((reference, pin)))
    # A proved connection is a defect even when its conductor obscures isolation.
    if net is None:
        return 'violation', 'native export lacks the declared no-connect membership'
    if net['terminals'] != ((reference, pin),):
        return 'violation', 'native export connects the declared no-connect pin'
    if not net['auto_unconnected_singleton']:
        return 'violation', 'native export lacks an auto-unconnected singleton'
    if not net['no_connect_marker']:
        return 'violation', 'native export lacks the explicit no-connect marker witness'
    geometry, problems = sheet_items(tree)
    targets = [item for item in geometry if item['kind'] == 'pin' and item['number'] == pin
               and field(item['node'], 'uuid', 1) == [comp['uuid']]]
    if len(targets) != 1:
        return 'unknown', 'no-connect pin has no unique captured source point'
    markers = [item for item in geometry if item['kind'] == 'no_connect'
               and item['shape'][0] == targets[0]['shape'][0]]
    if len(markers) != 1:
        return 'violation', 'physical pin requires one explicit no-connect marker'
    status, detail = marker_isolation(reference, geometry, problems, targets[0], pin)
    if status != 'disconnected':
        return 'unknown', detail
    return 'disconnected', detail


def _source_no_connects(tree, node, comp, graph):
    markers = {xy(children(marker, 'at')[0]) for marker in children(tree, 'no_connect')}
    geometry, _ = sheet_items(tree) if markers else ([], [])
    declared = {pin for pin in comp['pins']
                if comp['pin_metadata'][pin]['electrical_type'] == 'no_connect'}
    declared.update(item['number'] for item in geometry if item['kind'] == 'pin'
                    and item['node'] is node and item['shape'][0] in markers)
    for pin in sorted(declared):
        status, reason = _no_connect_status(tree, graph, comp['reference'], pin)
        if status != 'disconnected':
            raise ValueError(f'source no-connect {comp["reference"]}.{pin} is unproven: {reason}')
    return sorted(declared)


def no_connect_changes(selected, actual, tree, references):
    """Keep lost NC markers and newly connected NC pins as provenance defects."""
    changes = []
    for item in selected:
        ref = references[item['delivered']]
        for pin in item['no_connects']:
            status, reason = _no_connect_status(tree, actual, ref, pin)
            if status == 'unknown':
                raise ValueError(f'no-connect coverage unknown for {ref}.{pin}: {reason}')
            if status != 'disconnected':
                changes.append(dict(terminal=[ref, pin], severity='defect', before='no_connect',
                                    after=status, reason=reason))
    return changes


def models(directory, plan, exporter):
    """Bind every selected reference and pin to a fresh native source graph."""
    result = []
    uuids = set()
    for index, source in enumerate(plan['sources']):
        path = Path(directory) / source['sheet']
        if digest(path) != source['sha256']:
            raise ValueError('composition source bytes changed')
        raw = path.read_bytes()
        tree = parse_quoted(raw)
        if children(tree, 'sheet'):
            raise ValueError('nested composition sources are unsupported')
        symbols = sheet_symbols(raw, source['uuid_path'])
        graph = exporter(index, source['sheet'])
        used_nets = set()
        for ref, delivered in sorted(source['references'].items()):
            identities = symbols[ref]['uuids']
            if len(identities) != 1 or identities[0] in uuids:
                raise ValueError('composition supports unique single-unit source symbols only')
            uid = identities[0]
            uuids.add(uid)
            native = [c for c in graph['components'].values() if c['uuid'] == uid and c['sheet'] == '/']
            if len(native) != 1:
                raise ValueError('selected source symbol missing or ambiguous in native export')
            comp = native[0]
            node = next(n for n in children(tree, 'symbol') if field(n, 'uuid', 1) == [uid])
            no_connects = _source_no_connects(tree, node, comp, graph)
            nets = {}
            for pin in comp['pins']:
                if pin in no_connects:
                    continue
                nid = graph['memberships'].get((comp['reference'], pin))
                net = graph['nets'].get(nid)
                if net is None or net['name'] not in source['nets']:
                    raise ValueError('selected source pin lacks an explicit native net mapping')
                used_nets.add(net['name'])
                nets[pin] = source['nets'][net['name']]
            result.append(dict(source=source, reference=ref, delivered=delivered,
                               native=comp, node=node, tree=tree, nets=nets, no_connects=no_connects))
        if used_nets != set(source['nets']):
            raise ValueError('composition contains unused source net mappings')
    return result


def native_models(root, block_dir, plan):
    """Read selected components through private networkless native exports."""
    from . import native_run
    from .native_netlist import read_native_inventory
    with tempfile.TemporaryDirectory(prefix='agent-ee-block-composition-') as tmp:
        def export(index, sheet):
            work = Path(tmp) / str(index)
            work.mkdir()
            record = dict(native_run.run(native_run.netlist('/candidate/' + block_dir + '/' + sheet, '/work/design.xml',
                                                            timeout=120), inputs=Path(root), work=work))
            if record['execution'] != 'ok' or record['exit_code'] != 0:
                raise ValueError('composition source native export failed')
            return read_native_inventory(work / 'design.xml')
        return models(Path(root) / block_dir, plan, export)


def produce(root, block_dir, plan, occurrence, project):
    """Produce bytes before the destination write; private native export work only."""
    return emit(native_models(root, block_dir, plan), plan, occurrence, project)


def emit(selected, plan, occurrence, project):
    """Copy selected definitions/properties; label resolved pins, preserving topology."""
    def uid(key):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'agent-ee/block-composition/' + occurrence['instance_path'] + '/' + key))
    tree = parse_quoted(('(kicad_sch (version 20250114) (generator "eeschema") '
                         f'(uuid "{uid("sheet")}") (paper "A3") (lib_symbols))').encode())
    libraries = children(tree, 'lib_symbols')[0]
    embedded = {}
    for index, item in enumerate(selected):
        comp = item['native']
        lib_id = comp['lib_id']
        defs = children(children(item['tree'], 'lib_symbols')[0], 'symbol')
        definition = next(n for n in defs if n[1] == lib_id)
        if children(definition, 'extends'):
            raise ValueError('inherited composition symbol definitions are unsupported')
        if lib_id in embedded and embedded[lib_id] != definition:
            raise ValueError('composition symbol definitions conflict')
        if lib_id not in embedded:
            embedded[lib_id] = copy.deepcopy(definition)
            libraries.append(embedded[lib_id])
        node = copy.deepcopy(item['node'])
        # Existing instance fields and symbol properties survive; layout is regenerated.
        node[:] = [v for v in node if not (isinstance(v, list) and v[0] in ('instances', 'mirror', 'at'))]
        x, y = 70 + (index % 2) * 100, 50 + (index // 2) * 65
        node.append(['at', str(x), str(y), '0'])
        for prop in children(node, 'property'):
            if prop[1] == 'Reference':
                prop[2] = Quoted(item['delivered'])
            for at in children(prop, 'at'):
                at[1:] = [str(x), str(y - 8 if prop[1] == 'Reference' else y - 5), '0']
        node.append(['instances', ['project', Quoted(project),
            ['path', Quoted(occurrence['instance_path']), ['reference', Quoted(item['delivered'])], ['unit', '1']]]])
        model = _Symbol(node, occurrence, embedded, {})
        if (not model.resolved or model.power or model.unit != 1 or
                {p['number'] for p in model.pins} != set(comp['pins']) or
                len(model.pins) != len(comp['pins']) or any(p['hidden'] for p in model.pins)):
            raise ValueError('composition cannot establish all selected physical pin positions')
        tree.append(node)
        for pin in model.pins:
            px, py = map(_mm, pin['point'])
            marker = ['no_connect', ['at', px, py]] if pin['number'] in item['no_connects'] else [
                'label', Quoted(item['nets'][pin['number']]), ['at', px, py, '0'],
                ['effects', ['font', ['size', '1', '1']]]]
            tree.append([*marker, ['uuid', Quoted(uid(item['delivered'] + '/' + pin['number']))]])
    for index, port in enumerate(plan['ports']):
        x, y = '20', str(160 + index * 5)
        tree.append(['hierarchical_label', Quoted(port['name']), ['shape', port['direction']],
            ['at', x, y, '0'], ['effects', ['font', ['size', '1', '1']]], ['uuid', Quoted(uid('port/' + port['name']))]])
        tree.append(['label', Quoted(port['net_name']), ['at', x, y, '0'],
            ['effects', ['font', ['size', '1', '1']]], ['uuid', Quoted(uid('port-net/' + port['name']))]])
    return ('\n'.join(format_sexpr(tree)) + '\n').encode()


def compare(selected, actual, occurrence, bindings):
    """Exact selected physical inventory and all-pin topology; no informational drift."""
    keys = ('reference', 'uuid', 'lib_id', 'value', 'datasheet', 'footprint', 'populated',
            'on_board', 'in_bom', 'pins', 'units', 'pin_metadata', 'properties', 'library_pins')
    def physical(c):
        result = {k: c[k] for k in keys}
        result['properties'] = {k: v for k, v in c['properties'].items() if k not in ('Sheetname', 'Sheetfile')}
        return result
    expected = {}
    expected_nets = {}
    for item in selected:
        ref = item['delivered']
        expected[ref] = physical(item['native'])
        expected[ref]['reference'] = ref
        expected_nets.update({(ref, pin): bindings[net] for pin, net in item['nets'].items()})
    observed = {ref: physical(c) for ref, c in actual['components'].items() if c['sheet'] == occurrence['path']}
    component_changes = [{'reference': ref, 'severity': 'defect', 'before': expected.get(ref), 'after': observed.get(ref)}
                         for ref in sorted(expected.keys() | observed.keys()) if expected.get(ref) != observed.get(ref)]
    net_changes = no_connect_changes(selected, actual, occurrence['tree'],
                                     {item['delivered']: item['delivered'] for item in selected})
    for terminal, expected_name in sorted(expected_nets.items()):
        net = actual['nets'].get(actual['memberships'].get(terminal))
        name = net['name'] if net else None
        if name != expected_name:
            net_changes.append({'terminal': list(terminal), 'severity': 'defect', 'before': expected_name, 'after': name})
    return component_changes, net_changes
