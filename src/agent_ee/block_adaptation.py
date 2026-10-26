"""Instantiate captured blocks before capture; grade adaptations as ordinary checks."""

from .check_registry import register as _register_check
import argparse
import copy
import json
import shutil
import uuid
from pathlib import Path, PurePosixPath
from xml.etree.ElementTree import ParseError

from .candidate import safe_relative
from .hardware_interface import exact_keys, text
from .reference_blocks import _read, validate_block, hierarchy_ports
from .schematic_lint import load_sheets
from .storage import digest, write_json
from .symbol_library import children, field, parse_sexpr

INSTANCE_SCHEMA = 'agent-ee.block-instance/1'


def _path(value):
    text(value)
    path = safe_relative(value)
    if str(path) != value or '\\' in value:
        raise ValueError('noncanonical instance path')
    return path


def _local(root, value):
    path = Path(root) / _path(value)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('instance paths cannot contain symlinks')
    return path


def _source(root, block, composition=None):
    directory = _local(root, block)
    # Referenced runs are deliberately not opened. They cannot qualify a board.
    validate_block(directory)
    record = _read(directory / 'block.json')
    if composition is not None:
        from .block_composition import validate
        plan = _read(_local(root, composition))
        record['boundary_ports'] = validate(plan, record)
        record['_composition'] = plan
        return record
    if len(record.get('instances', [])) > 1:
        raise ValueError('multiple source instances are not supported by block adaptation')
    ast = parse_sexpr((directory / record['sheet']).read_bytes())
    if children(ast, 'sheet'):
        raise ValueError('nested source blocks are not supported')
    if not record['boundary_ports']:
        raise ValueError('block adaptation requires boundary ports')
    return record


def _instance(root, rel):
    record = _read(_local(root, rel))
    if record.get('schema') == 'agent-ee.flat-block-instance/1':
        from .block_flat import read_instance
        return read_instance(root, record)
    exact_keys(record, 'schema id block block_id version sha256 sheet instance_path bindings requirements' +
               (' composition' if 'composition' in record else ''))
    if record['schema'] != INSTANCE_SCHEMA:
        raise ValueError('unknown block instance schema')
    from .findings import safe_id
    safe_id(record['id'], 'instance id')
    for key in ('block', 'sheet'):
        _path(record[key])
    if not isinstance(record['instance_path'], str) or not record['instance_path'].startswith('/') or not record['instance_path'].endswith('/'):
        raise ValueError('instance_path must be a native sheet UUID path')
    segments = record['instance_path'].strip('/').split('/')
    if not all(str(uuid.UUID(s)) == s for s in segments):
        raise ValueError('instance_path requires canonical UUIDs')
    composition = record.get('composition')
    if composition is not None:
        exact_keys(composition, 'path sha256 emitted_sha256')
        if digest(_local(root, composition['path'])) != composition['sha256']:
            raise ValueError('composition plan hash differs')
        from .reference_blocks import _hash
        _hash(composition['emitted_sha256'])
    source = _source(root, record['block'], composition['path'] if composition else None)
    if (record['block_id'], record['version'], record['sha256']) != (
            source['id'], source['version'], source['files'][source['sheet']]):
        raise ValueError('block identity differs from instance record')
    bindings = record['bindings']
    if not isinstance(bindings, dict) or set(bindings) != {p['name'] for p in source['boundary_ports']}:
        raise ValueError('every boundary port needs exactly one immutable binding')
    for net in bindings.values():
        text(net)
    if '_composition' in source:
        for port in source['_composition']['ports']:
            if bindings[port['name']] != bindings[port['net_name']]:
                raise ValueError('composition port alias binding differs')
        canonical = [p['name'] for p in source['_composition']['ports'] if p['alias_of'] is None]
        if len({bindings[p] for p in canonical}) != len(canonical):
            raise ValueError('composition bindings cannot merge distinct source nets')
    mapping = record['requirements']
    if not isinstance(mapping, dict) or set(mapping) != {r['id'] for r in source['requirements']}:
        raise ValueError('every block requirement needs a board check mapping')
    return record, source


def _requirement_intent(source, requirement):
    # A captured netlist-only source requirement is informational by contract.
    # Preserve this distinction when composing a block; it cannot become a
    # synthetic acceptance witness just because it was instantiated.
    if '_composition' in source:
        kinds = {c['id']: c['kind'] for c in source.get('check_set', [])}
        if requirement['checks'] and all(kinds.get(c) == 'netlist' for c in requirement['checks']):
            return 'informational'
    return 'acceptance'


def capability(params, scope, files, inputs, check_id):
    exact_keys(params, 'instance netlist_check')
    rel = str(_path(params['instance']))
    if inputs is None or rel not in files:
        raise ValueError('instance record must be captured')
    record, source = _instance(inputs, rel)
    if 'composition' in record and scope.get('schema') != 'agent-ee.scope/3':
        raise ValueError('selected-reference composition requires scope/3')
    dependencies = [rel, str(PurePosixPath(record['block']) / 'block.json')]
    dependencies += [str(PurePosixPath(record['block']) / p) for p in source['files']]
    if 'composition' in record:
        dependencies.append(record['composition']['path'])
    for p in dependencies:
        if p not in files or files[p] != digest(_local(inputs, p)):
            raise ValueError('block dependency not captured or changed: ' + p)
    if record['sheet'] not in files or scope.get('schematic') not in files:
        raise ValueError('instance and board schematic must be captured')
    if record['sheet'] in dependencies or scope['schematic'] in dependencies:
        raise ValueError('block sources cannot be mutable design sheets')
    specs = scope['checks']
    prior = specs[:next(i for i, s in enumerate(specs) if s['id'] == check_id)]
    if not any(s['id'] == params['netlist_check'] and s['kind'] == 'netlist' for s in prior):
        raise ValueError('board netlist check must precede block_provenance')
    requirements = scope.get('baseline', {}).get('requirements', [])
    for req in source['requirements']:
        checks = record['requirements'][req['id']]
        if (not isinstance(checks, list) or not checks or len(checks) != len(set(checks)) or
                any(c not in {s['id'] for s in specs if s['kind'] != 'block_provenance'} for c in checks)):
            raise ValueError('block requirements require fresh board checks other than provenance')
        expected = {'id': record['id'] + '-' + req['id'], 'statement': req['statement'],
            'source': {'path': str(PurePosixPath(record['block']) / 'block.json'),
                       'locator': 'requirements/' + req['id']}, 'checks': checks}
        if scope.get('schema') == 'agent-ee.scope/3':
            expected['intent'] = _requirement_intent(source, req)
            if expected['intent'] == 'informational' and any(
                    next(s['kind'] for s in specs if s['id'] == c) != 'netlist' for c in checks):
                raise ValueError('native source requirement must map to fresh native netlist checks')
        if expected not in requirements:
            raise ValueError('block requirement mapping differs from board baseline')
    predicates = ['block-boundary:' + p['name'] for p in source['boundary_ports']]
    if '_composition' in source:
        predicates += (['block-flat:component-adaptations', 'block-flat:pin-net-adaptations']
                       if record['schema'] == 'agent-ee.flat-block-instance/1' else
                       ['block-composition:components', 'block-composition:pin-nets'])
    return {'instance': record, 'block': source, 'immutable_inputs': dependencies,
            'predicates': predicates}


def instantiate(root, scope, request):
    """Fill one pre-created child and return a new scope; caller captures the tree.

    The caller owns the scaffold and is its only writer. Source blocks must
    already be present beneath root. No campaign, evidence store or run is made.
    """
    exact_keys(request, 'id block sheet instance_path bindings requirements record provenance_check netlist_check' +
               (' composition' if 'composition' in request else ''))
    root = Path(root)
    if scope.get('schema') not in ('agent-ee.scope/2', 'agent-ee.scope/3'):
        raise ValueError('block instances require an immutable scope/2 or scope/3 baseline')
    source = _source(root, request['block'], request.get('composition'))
    if 'composition' in request and scope['schema'] != 'agent-ee.scope/3':
        raise ValueError('selected-reference composition requires scope/3')
    sheet = _local(root, request['sheet'])
    old = sheet.read_bytes()
    ast = parse_sexpr(old)
    if ast[0] != 'kicad_sch' or any(children(ast, k) for k in (
            'symbol', 'wire', 'label', 'global_label', 'hierarchical_label', 'sheet', 'bus', 'bus_entry',
            'junction', 'no_connect', 'bus_alias', 'netclass_flag')):
        raise ValueError('block destination must be a pre-created empty sheet')
    sheets = load_sheets(root, scope['schematic'])
    matches = [s for s in sheets if s['file'] == request['sheet']]
    if len(matches) != 1 or matches[0]['path'] != request['instance_path'] or matches[0]['path'] == '/':
        raise ValueError('destination must be exactly one pre-created child occurrence')
    record_path = _local(root, request['record'])
    if record_path.exists() or not record_path.parent.is_dir():
        raise ValueError('instance record requires a new file in an existing directory')
    record = {k: copy.deepcopy(request[k]) for k in ('id', 'block', 'sheet', 'instance_path', 'bindings', 'requirements')}
    record.update(schema=INSTANCE_SCHEMA, block_id=source['id'], version=source['version'],
                  sha256=source['files'][source['sheet']])
    result = copy.deepcopy(scope)
    cid = request['provenance_check']
    from .findings import safe_id
    safe_id(cid, 'provenance check id')
    if cid in {s['id'] for s in result['checks']}:
        raise ValueError('duplicate provenance check id')
    result['checks'].append({'id': cid, 'kind': 'block_provenance',
        'params': {'instance': request['record'], 'netlist_check': request['netlist_check']}})
    baseline = result['baseline']
    for req in source['requirements']:
        mapped = {'id': request['id'] + '-' + req['id'], 'statement': req['statement'],
            'source': {'path': str(PurePosixPath(request['block']) / 'block.json'),
                       'locator': 'requirements/' + req['id']},
            'checks': copy.deepcopy(request['requirements'].get(req['id']))}
        if result['schema'] == 'agent-ee.scope/3':
            mapped['intent'] = _requirement_intent(source, req)
        baseline['requirements'].append(mapped)
    bound = {'id': request['id'] + '-boundary', 'statement': 'Preserve the captured block boundary bindings',
        'source': {'path': request['record'], 'locator': 'bindings'}, 'checks': [cid]}
    if '_composition' in source:
        bound['statement'] = 'Preserve selected source component identities, pin connectivity and captured block boundary bindings'
    if result['schema'] == 'agent-ee.scope/3':
        bound['intent'] = 'acceptance'
    baseline['requirements'].append(bound)
    baseline['reference_architectures'].append(str(PurePosixPath(request['block']) / 'block.json'))
    mutable = result.setdefault('mutable_inputs', [p for p in (result.get('board'), result['schematic']) if p])
    if request['sheet'] not in mutable:
        mutable.append(request['sheet'])
    raw = (_local(root, request['block']) / source['sheet']).read_bytes()
    if 'composition' in request:
        from .block_composition import produce
        from hashlib import sha256
        raw = produce(root, request['block'], source['_composition'], matches[0], PurePosixPath(scope['schematic']).stem)
        record['composition'] = {'path': request['composition'],
            'sha256': digest(_local(root, request['composition'])), 'emitted_sha256': sha256(raw).hexdigest()}
    try:
        sheet.write_bytes(raw)
        write_json(record_path, record)
        files = {p.relative_to(root).as_posix(): digest(p) for p in root.rglob('*') if p.is_file()}
        from .baseline import compile_baseline
        baseline_record = compile_baseline(result, files, root)
        if set(mutable) & baseline_record['files'].keys():
            raise ValueError('mutable_inputs overlap immutable block provenance')
    except BaseException:
        sheet.write_bytes(old)
        record_path.unlink(missing_ok=True)
        raise
    return result


# A passive one-pin witness at each native hierarchical label observes the net
# KiCad assigns that port. The instrumented export must preserve every original
# physical component and net membership before it can supply a boundary witness.
def _probe_sheet(raw, occurrence, project):
    from .schematic_emit import parse_quoted, format_sexpr
    tree = parse_quoted(raw)
    libs = children(tree, 'lib_symbols')
    if len(libs) != 1:
        raise ValueError('boundary probing requires one embedded symbol library')
    lib_id = 'AgentEEBoundary:Probe'
    if any(s[1] == lib_id for s in children(libs[0], 'symbol')):
        raise ValueError('reserved boundary probe library identity is already present')
    definition = b'''(symbol "AgentEEBoundary:Probe" (pin_names (offset 0)) (in_bom no) (on_board no)
      (property "Reference" "Z" (at 0 0 0) (effects (font (size 1 1))))
      (property "Value" "boundary" (at 0 0 0) (effects (font (size 1 1))))
      (symbol "Probe_1_1" (pin passive line (at 0 0 0) (length 0)
        (name "port" (effects (font (size 1 1)))) (number "1" (effects (font (size 1 1)))))))'''
    libs[0].append(parse_quoted(definition))
    anchors = {}
    namespace = uuid.UUID('25fe8a6e-b9cc-44e1-826a-f74be3a876ee')
    for index, label in enumerate(children(tree, 'hierarchical_label')):
        uid = str(uuid.uuid5(namespace, occurrence['instance_path'] + '/' + label[1]))
        if uid.encode() in raw:
            raise ValueError('boundary probe UUID collision')
        ref = f'Z_AEE_BOUNDARY_{index}'
        if any(p[2] == ref for s in children(tree, 'symbol') for p in children(s, 'property') if p[1] == 'Reference'):
            raise ValueError('boundary probe reference collision')
        x, y, _ = field(label, 'at', 3)
        q = json.dumps
        symbol = f'''(symbol (lib_id "{lib_id}") (at {x} {y} 0) (unit 1)
          (in_bom no) (on_board no) (dnp no) (uuid "{uid}")
          (property "Reference" "{ref}" (at 0 0 0) (effects (font (size 1 1))))
          (property "Value" "boundary" (at 0 0 0) (effects (font (size 1 1))))
          (instances (project {q(project)} (path {q(occurrence['instance_path'])} (reference "{ref}") (unit 1)))))'''
        tree.append(parse_quoted(symbol.encode()))
        anchors[label[1]] = uid
    return ('\n'.join(format_sexpr(tree)) + '\n').encode(), anchors


def _physical(graph, omit=()):
    components = {}
    for ref, comp in graph['components'].items():
        if ref in omit:
            continue
        item = {k: v for k, v in comp.items() if k != 'locator'}
        item['properties'] = {k: v for k, v in item['properties'].items()
                              if k not in ('Sheetname', 'Sheetfile')}
        components[ref] = item
    nets = sorted((net['name'], sorted(t for t in net['terminals'] if t[0] not in omit))
                  for net in graph['nets'].values() if any(t[0] not in omit for t in net['terminals']))
    return components, nets


def semantic_diff(source, actual, occurrence, bindings, block):
    """Compare native physical instances by UUID, retaining values and pin sets."""
    keys = ('reference', 'lib_id', 'value', 'footprint', 'populated', 'on_board', 'in_bom',
            'pins', 'units', 'pin_metadata', 'properties')
    def components(graph, path):
        result = {}
        for c in graph['components'].values():
            if c['sheet'] != path:
                continue
            item = {k: c[k] for k in keys}
            item['properties'] = {k: v for k, v in item['properties'].items()
                                  if k not in ('Sheetname', 'Sheetfile')}
            result[c['uuid']] = item
        return result
    before, after = components(source, '/'), components(actual, occurrence['path'])
    changes = [{'uuid': uid, 'severity': 'info', 'before': before.get(uid), 'after': after.get(uid)}
               for uid in sorted(before.keys() | after.keys()) if before.get(uid) != after.get(uid)]
    def nets(graph, path, normalize):
        result = {}
        for net in graph['nets'].values():
            terminals = sorted((graph['components'][r]['uuid'], p) for r, p in net['terminals']
                               if graph['components'][r]['sheet'] == path)
            if terminals:
                result[normalize(net['name'])] = terminals
        return result
    names = {p['net_name']: bindings[p['name']] for p in block['boundary_ports']}
    def source_name(name):
        return names.get(name, names.get(name.removeprefix('/'),
            occurrence['names'] + name[1:] if name.startswith('/') else name))
    bn, an = nets(source, '/', source_name), nets(actual, occurrence['path'], lambda n: n)
    net_changes = [{'net': name, 'severity': 'info', 'before': bn.get(name), 'after': an.get(name)}
                   for name in sorted(bn.keys() | an.keys()) if bn.get(name) != an.get(name)]
    return changes, net_changes


def _export(ctx, label, schematic, extra_ro=()):
    from .checks import CheckError
    from .executor import execute
    from .native_netlist import read_native_inventory
    work = ctx.work / label
    if work.exists():
        raise CheckError('malformed', 'native export workspace already exists: ' + label)
    work.mkdir()
    remaining = ctx.remaining()
    if remaining <= 1:
        raise CheckError('interrupted', 'deadline exhausted before ' + label + ' export')
    record = execute(ctx.inputs, work, ['kicad-cli', 'sch', 'export', 'netlist', '--format',
        'kicadxml', '-o', '/work/design.xml', schematic], timeout=min(120, remaining), extra_ro=extra_ro)
    ctx.evidence(*(label + '/' + name for name in ('execution.json', 'console.log', 'design.xml')))
    if record['execution'] == 'timeout' and remaining < 120:
        raise CheckError('interrupted', 'deadline cut off ' + label + ' export')
    if record['execution'] != 'ok' or record['exit_code'] != 0:
        raise CheckError(record['execution'] if record['execution'] != 'ok' else 'failed', label + ' native export failed')
    if (work / 'design.xml').is_symlink():
        raise CheckError('malformed', label + ' native output is a symlink')
    return read_native_inventory(work / 'design.xml')


def check_block_provenance(ctx):
    from .checks import CheckError, parse_xml_netlist
    from .datasheet_facts import prerequisite
    from .native_netlist import read_native_inventory
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
        record, block = descriptor['instance'], descriptor['block']
        if record['schema'] == 'agent-ee.flat-block-instance/1':
            from .block_flat import check
            return check(ctx, descriptor)
        xml = prerequisite(ctx, ctx.params['netlist_check'], 'netlist', 'design.xml')
        connectivity = prerequisite(ctx, ctx.params['netlist_check'], 'netlist', 'connectivity.json')
        actual = read_native_inventory(xml)
        if _read(connectivity) != parse_xml_netlist(xml):
            raise ValueError('board connectivity projection differs from native XML')
        sheets = load_sheets(ctx.inputs, ctx.scope['schematic'])
        producer = _read(ctx.run_dir / 'checks' / (ctx.params['netlist_check'] + '.json'))
        if len(actual['sheets']) != len(sheets) or len(sheets) != producer['details']['sheet_instances']:
            raise ValueError('board native hierarchy coverage differs')
        matches = [s for s in sheets if s['path'] == record['instance_path'] and s['file'] == record['sheet']]
        if len(matches) != 1 or sum(s['file'] == record['sheet'] for s in sheets) != 1:
            raise ValueError('recorded child occurrence is missing or ambiguous')
        occurrence = matches[0]
        if children(occurrence['tree'], 'sheet'):
            raise ValueError('nested adapted blocks are not supported')
        composition = block.get('_composition')
        if composition is None:
            source = _export(ctx, 'source', '/candidate/' + str(PurePosixPath(record['block']) / block['sheet']))
            component_changes, net_changes = semantic_diff(source, actual, occurrence, record['bindings'], block)
        else:
            from .block_composition import models, compare
            selected = models(ctx.inputs / record['block'], composition,
                lambda i, path: _export(ctx, 'source-' + str(i),
                    '/candidate/' + str(PurePosixPath(record['block']) / path)))
            component_changes, net_changes = compare(selected, actual, occurrence, record['bindings'])
        ports = hierarchy_ports((ctx.inputs / record['sheet']).read_bytes())
        parent_path = '/' + '/'.join(record['instance_path'].strip('/').split('/')[:-1])
        parent_path = parent_path.rstrip('/') + '/'
        parent = next(s for s in sheets if s['path'] == parent_path)
        sheet_uuid = record['instance_path'].strip('/').split('/')[-1]
        symbol = next(n for n in children(parent['tree'], 'sheet') if field(n, 'uuid', 1) == [sheet_uuid])
        pins = {}
        for pin in children(symbol, 'pin'):
            if len(pin) < 3 or pin[1] in pins:
                raise ValueError('duplicate or malformed parent boundary pin')
            pins[pin[1]] = pin[2]
        boundary = []
        for p in block['boundary_ports']:
            present = ports.get(p['name']) == pins.get(p['name']) == p['direction']
            boundary.append({'port': p['name'], 'expected_net': record['bindings'][p['name']],
                'status': 'pending' if present else 'violation',
                'reason': '' if present else 'child port or parent pin missing or direction changed'})
        extra = (set(ports) | set(pins)) - {p['name'] for p in block['boundary_ports']}
        if extra:
            boundary += [{'port': p, 'status': 'violation', 'reason': 'undeclared boundary port'} for p in sorted(extra)]
        # Observe the actual native net at each remaining child port, including
        # disconnected or moved labels. A shadow sheet cannot supply this witness.
        probe_root = ctx.work / 'probe-inputs'
        probe_root.mkdir()
        for sheet in sheets:
            dst = probe_root / sheet['file']
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.inputs / sheet['file'], dst)
            ctx.evidence('probe-inputs/' + sheet['file'])
        raw, anchors = _probe_sheet((ctx.inputs / record['sheet']).read_bytes(), occurrence,
                                    PurePosixPath(ctx.scope['schematic']).stem)
        (probe_root / record['sheet']).write_bytes(raw)
        probed = _export(ctx, 'boundary', '/probe/' + ctx.scope['schematic'], [(probe_root, '/probe')])
        probes = {ref: c for ref, c in probed['components'].items()
                  if c['sheet'] == occurrence['path'] and c['uuid'] in anchors.values()}
        if len(probes) != len(anchors) or _physical(probed, probes) != _physical(actual):
            raise ValueError('boundary probes did not preserve the native physical graph')
        by_uuid = {c['uuid']: ref for ref, c in probes.items()}
        for port in boundary:
            if port['status'] != 'pending':
                continue
            ref = by_uuid[anchors[port['port']]]
            nid = probed['memberships'].get((ref, '1'))
            net = probed['nets'].get(nid)
            name = net['name'] if net else None
            connected = net is not None and any(r not in probes for r, _ in net['terminals'])
            port.update(observed_net=name, probe_uuid=anchors[port['port']],
                status='satisfied' if connected and name == port['expected_net'] else 'violation',
                reason='' if connected and name == port['expected_net'] else 'boundary binding missing or rebound')
        report = {'schema': 'agent-ee.block-provenance/1', 'candidate_id': ctx.manifest['candidate_id'],
            'instance': record, 'component_changes': component_changes, 'net_changes': net_changes,
            'boundary': boundary, 'same_bytes': digest(ctx.inputs / record['sheet']) ==
                (record['composition']['emitted_sha256'] if composition else record['sha256']),
            'source_evidence': 'provenance_only', 'native_xml_sha256': digest(xml)}
        if composition:
            report['composition'] = {'plan': record['composition'], 'sources': composition['sources'],
                'ports': composition['ports'], 'source_commit': block['source']['commit']}
        report = json.loads(json.dumps(report))
        write_json(ctx.work / 'provenance.json', report)
        ctx.evidence('provenance.json')
        ctx.result['details'].update(report)
        defects = [p for p in boundary if p['status'] == 'violation']
        if composition:
            defects += component_changes + net_changes
        same = report['same_bytes']
        summary = (f"same as block v{record['version']}, sha256 {record['sha256']}" if same else
                   f"adapted block v{record['version']}: {len(component_changes)} component and {len(net_changes)} net changes")
        if composition:
            summary = (f"selected-reference composition from {len(composition['sources'])} captured instances; "
                       f"{len(component_changes)} component and {len(net_changes)} pin-net changes")
        ctx.result.update(execution='ok', outcome='defect' if defects else 'pass',
                          summary=summary + f'; {len(defects)} ' + ('provenance/boundary defects' if composition else 'boundary defects'))
        ctx.result['limitations'].append('Provenance and boundary bindings only; source runs supply no board acceptance. '
            'Flat source blocks and one child-file occurrence only. Board requirements need fresh checks.')
        if composition:
            ctx.result['limitations'].append('Selected-reference composition preserves declared source nets, '
                'including explicit aliases; no source-review or block-verification gate is satisfied by provenance.')
    except CheckError:
        raise
    except (OSError, ValueError, KeyError, TypeError, StopIteration, ParseError) as exc:
        raise CheckError('unsupported', str(exc)) from exc


def compose_flat(root, intent, blocks, bindings, *, captured_libraries):
    """Return intent/1, captured libraries and immutable block-instance sidecars.

    Does not publish files. All block sources must already be captured under root.
    The completed board intent, including real surrounding terminals, must resolve.
    """
    from .block_flat import compose
    return compose(root, intent, blocks, bindings, captured_libraries)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--scope', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.out.exists() or args.out.is_symlink():
        parser.error('--out must be a new scope file')
    request = _read(args.request)
    if args.out.resolve() == _local(args.candidate, request['record']).resolve():
        parser.error('--out cannot overwrite the instance record')
    result = instantiate(args.candidate, _read(args.scope), request)
    for spec in result['inputs']:
        source = Path(spec['path']).expanduser()
        if not source.is_absolute():
            spec['path'] = str((args.scope.resolve().parent / source).resolve())
    write_json(args.out, result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


def describe_block_provenance(ctx):
    descriptor = capability(ctx.params, ctx.scope, ctx.files, ctx.inputs, ctx.spec['id'])
    ctx.prerequisite(ctx.params.get('netlist_check'), ('netlist',))
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('block_provenance', describe_block_provenance, check_block_provenance)
