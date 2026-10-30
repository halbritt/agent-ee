"""Instantiate captured blocks before capture; grade adaptations as ordinary checks."""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires
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
        record['_captured_boundary_ports'] = copy.deepcopy(record['boundary_ports'])
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
    if record['instance_path'] != '/' and not all(str(uuid.UUID(s)) == s for s in segments):
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
    if record['instance_path'] == '/' and not (composition is not None
            and len(source.get('instances', [])) == 1 and source['instances'][0]['uuid_path'] == '/'
            and source.get('_captured_boundary_ports') == []):
        raise ValueError('root composition requires one explicit root source without hierarchical boundary ports')
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
    _mapping(source, record['requirements'])
    return record, source


def _requirement_intent(source, requirement):
    # A captured netlist-only source requirement is informational by contract.
    # Preserve this distinction when composing a block; it cannot become a
    # synthetic acceptance witness just because it was instantiated.
    if requirement.get('verification_phase') == 'adaptation':
        return 'acceptance'
    if '_composition' in source:
        kinds = {c['id']: c['kind'] for c in source.get('check_set', [])}
        if requirement['checks'] and all(kinds.get(c) == 'netlist' for c in requirement['checks']):
            return 'informational'
    return 'acceptance'


def _mapping(source, mapping):
    if (source.get('pending_requirements') or not isinstance(mapping, dict)
            or set(mapping) != {r['id'] for r in source['requirements']}):
        raise ValueError('every source and adaptation requirement needs a complete fresh check mapping; pending cannot be dropped')
    for checks in mapping.values():
        if (not isinstance(checks, list) or not checks or any(not isinstance(c,str) or not c for c in checks)
                or len(set(checks)) != len(checks)):
            raise ValueError('requirement mappings require nonempty unique check IDs')


def _rebound(value, names, requirements, checks):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            target = names.get(key,key)
            if key in ('mpn','manufacturer','package','footprint','value','equals','subcircuit','pin_map'):
                result[target] = copy.deepcopy(item)
            elif key in ('requirement','requirement_id'):
                result[target] = requirements.get(item,item)
            elif key.endswith('_check'):
                result[target] = checks.get(item,item)
            elif key == 'requirement_sources':
                result[target] = {requirements.get(r,r):_rebound(v,names,requirements,checks) for r,v in item.items()}
            else:
                result[target] = _rebound(item,names,requirements,checks)
        if len(result) != len(value):
            raise ValueError('ambiguous rebinding aliases')
        return result
    if isinstance(value, list):
        return [_rebound(v,names,requirements,checks) for v in value]
    return names.get(value,value) if isinstance(value,str) else value


def _interface_addresses(document, names, requirements):
    """Rebind physical addresses, retaining logical terminal/invariant identities."""
    result=copy.deepcopy(document)
    for component in result['components'].values():
        component['locations']=[dict(sheet=names.get(p['sheet'],p['sheet']),
            reference=names.get(p['reference'],p['reference'])) for p in component['locations']]
        component['symbols']=[names.get(symbol,symbol) for symbol in component['symbols']]
    for row in result['invariants']:
        row['requirement']=requirements.get(row['requirement'],row['requirement'])
        if 'names' in row:
            row['names']=[names.get(net,net) for net in row['names']]
        if 'io_map' in row:
            row['io_map']['path']=names.get(row['io_map']['path'],row['io_map']['path'])
    if 'qualification_record' in result:
        result['qualification_record']['path']=names.get(result['qualification_record']['path'],result['qualification_record']['path'])
    return result


def _part_allocations(document, names):
    # Purchasing identity and logical IDs are literal. Only CAD library aliases
    # and physical instance addresses change when the adapter renumbers a part.
    fields=('id','role','required','lib_id','footprint','lcsc','mpn','manufacturer','instances')
    rows=[]
    for part in document['parts']:
        row={key:copy.deepcopy(part[key]) for key in fields}
        row['lib_id']=names.get(part['lib_id'],part['lib_id'])
        row['instances']=[dict(sheet=names.get(i['sheet'],i['sheet']),
                               reference=names.get(i['reference'],i['reference'])) for i in part['instances']]
        if 'package' in part:
            row['package']=part['package']
        rows.append(row)
    return sorted(rows,key=lambda r:r['id'])


def _fresh_contract(root, source, record, scope, files, native_id):
    """Replay declared criteria under the adapter's captured subject bindings."""
    declared = {c['id']:c for c in source.get('check_set', [])}
    if not declared:
        return  # Historical blocks without templates keep their existing semantics.
    targets = {c['id']:c for c in scope['checks']}
    requirements = {r['id']:record['id']+'-'+r['id'] for r in source['requirements']}
    names = {}
    checks = {}
    for req in source['requirements']:
        old, fresh = req['checks'], record['requirements'][req['id']]
        if len(old) != len(fresh):
            raise ValueError('fresh mapping must preserve every declared check contract')
        for before, after in zip(old,fresh):
            if before not in declared or (before in checks and checks[before] != after):
                raise ValueError('source check contract missing or ambiguously mapped')
            checks[before] = after
    if len(set(checks.values())) != len(checks):
        raise ValueError('distinct declared check contracts cannot collapse into one check')
    block_path = str(PurePosixPath(record['block'])/'block.json')
    names.update({p:str(PurePosixPath(record['block'])/p) for p in source['files']})
    names.update({'/candidate/'+p:'/candidate/'+str(PurePosixPath(record['block'])/p) for p in source['files']})
    if '_composition' in source:
        plan=source['_composition']
        if record.get('schema') == 'agent-ee.flat-block-instance/1':
            from .block_flat import _library_id
            from .reference_blocks import sheet_symbols
            for occurrence in plan['sources']:
                for symbol in sheet_symbols((_local(root,record['block'])/occurrence['sheet']).read_bytes(),occurrence['uuid_path']).values():
                    names[symbol['lib_id']]=_library_id(record['id'],symbol['lib_id'])
        for occurrence in plan['sources']:
            names[occurrence['uuid_path']]=record['instance_path']
            for old, delivered in occurrence['references'].items():
                names[old]=record.get('references',{}).get(delivered,delivered)
        for port in plan['ports']:
            target=record.get('native_bindings',record['bindings'])[port['name']]
            names[port['net_name']]=target
            names['/'+port['net_name'].lstrip('/')]=target
        for occurrence in plan['sources']:
            instance=next(i for i in source['instances'] if i['sheet']==occurrence['sheet'] and i['uuid_path']==occurrence['uuid_path'])
            for original, port in occurrence['nets'].items():
                target=record.get('native_bindings',record['bindings'])[port]
                names[original]=target
                if original.startswith('/'):
                    names[instance['path'].rstrip('/')+original]=target
    else:
        names['/']=record['instance_path']
        if source.get('instances'):
            names[source['instances'][0]['uuid_path']]=record['instance_path']
        names.update({p['net_name']:record['bindings'][p['name']] for p in source['boundary_ports']})
    # New assets must retain the exact algebra/invariants, changing only captured
    # instance bindings and the fresh requirement citations required by their APIs.
    for old, fresh in list(checks.items()):
        original, actual = declared[old], targets.get(fresh,{})
        if actual.get('kind') != original['kind'] or actual.get('kind') == 'block_provenance':
            raise ValueError('fresh check family differs from declared source contract')
        params=copy.deepcopy(original['params'])
        if 'netlist_check' in params:
            if checks.get(params['netlist_check'],native_id) != native_id:
                raise ValueError('declared netlist prerequisite mapping differs')
            checks[params['netlist_check']]=native_id
        expected=_rebound(params,names,requirements,checks);observed=copy.deepcopy(actual.get('params',{}))
        if original['kind'] == 'parts_subset':
            from .parts_manifest import load_manifest
            from .reference_blocks import sheet_symbols
            source_parts=load_manifest(root,str(PurePosixPath(record['block'])/params['manifest']))
            board_parts=load_manifest(root,observed['manifest'])
            if (source_parts['property_fields'] != board_parts['property_fields']
                    or _part_allocations(source_parts,names) != _part_allocations(board_parts,{})):
                raise ValueError('fresh membership changes captured allocation/identity contract')
            if 'reference_set' in params:
                from .parts_subset import reference_set
                selected=reference_set(params['reference_set'])
                if record['instance_path']=='/':
                    # Root roles retain captured physical identities. The scaffold
                    # is empty; root_coverage checks their exact native union later.
                    expected['reference_set']=root_membership(source)
                else:
                    authored=sheet_symbols(_local(root,record['sheet']).read_bytes(),record['instance_path'])
                    expected['reference_set']=[]
                    for row in selected:
                        reference=names.get(row['reference'],row['reference'])
                        if reference not in authored:
                            raise ValueError('adapted membership reference absent from authored current sheet')
                        expected['reference_set'].append(dict(sheet=record['instance_path'],reference=reference,
                            uuids=sorted(authored[reference]['uuids'])))
                    expected['reference_set'].sort(key=lambda r:(r['sheet'],r['reference']))
            observed['manifest']=expected['manifest']
        if original['kind'] == 'active_spice':
            expected['contract'] = params['contract']  # A version token, never an asset path.
        asset_fields = {'hardware_interface':('contract',), 'interval_assertions':('assertions',), 'topology_rules':('rules',)}.get(original['kind'], ())
        for field in asset_fields:
            before=_read(_local(root,str(PurePosixPath(record['block'])/params[field])))
            after=_read(_local(root,observed[field]))
            before=(_interface_addresses(before,names,requirements) if field=='contract'
                    else _rebound(before,names,requirements,checks))
            if field=='rules':
                owners=[r for r in source['requirements'] if old in r['checks']]
                if len(owners)!=1:
                    raise ValueError('rules require one exact captured requirement')
                for row in before['rules']:
                    row['requirement']=requirements[owners[0]['id']]
            if field=='contract':
                for row in before['invariants']:
                    rid=row['requirement']
                    row['source']=dict(path=block_path,locator='requirements/'+rid.removeprefix(record['id']+'-'))
                    observed_source=next((i['source'] for i in after['invariants'] if i['id']==row['id']),{})
                    if 'sha256' in observed_source:
                        row['source']['sha256']=digest(_local(root,block_path))
            elif field == 'assertions':
                before['requirement_sources']={requirements[r['id']]:dict(path=block_path,
                    locator='requirements/'+r['id'],sha256=digest(_local(root,block_path)))
                    for r in source['requirements'] if requirements[r['id']] in before.get('requirement_sources',{})}
            if before != after:
                raise ValueError('fresh check asset changes declared criteria/source/conditions')
            observed[field]=expected[field]
        if observed != expected:
            raise ValueError('fresh check parameters differ from rebound declared criteria')
        from .capabilities import describe_check
        descriptor=describe_check(actual,check_specs=scope['checks'],files=files,scope=scope,inputs=root)
        requirement=next(r for r in source['requirements'] if old in r['checks'])
        if descriptor['status'] != 'supported' or (_requirement_intent(source,requirement)=='acceptance' and not descriptor['can_evaluate_bound']):
            raise ValueError('fresh declared check has no executable acceptance coverage: '+str(descriptor['unsupported_reasons']))


def baseline_rows(record, source):
    """Fresh requirements sourced to the immutable captured block, never old passes."""
    return [dict(id=record['id']+'-'+r['id'], statement=r['statement'],
        source=dict(path=str(PurePosixPath(record['block'])/'block.json'), locator='requirements/'+r['id']),
        checks=copy.deepcopy(record['requirements'][r['id']]), intent=_requirement_intent(source,r))
        for r in source['requirements']]


def capability(params, scope, files, inputs, check_id):
    exact_keys(params, 'instance netlist_check')
    rel = str(_path(params['instance']))
    if inputs is None or rel not in files:
        raise ValueError('instance record must be captured')
    record, source = _instance(inputs, rel)
    if any(r.get('verification_phase') == 'adaptation' for r in source['requirements']) and scope.get('schema') != 'agent-ee.scope/3':
        raise ValueError('adaptation-phase requirements require fresh executable scope/3 acceptance')
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
        expected = next(r for r in baseline_rows(record, source) if r['id'] == record['id']+'-'+req['id'])
        if expected.get('intent') == 'informational' and any(
                next(s['kind'] for s in specs if s['id'] == c) != 'netlist' for c in checks):
            raise ValueError('native source requirement must map to fresh native netlist checks')
        if scope.get('schema') != 'agent-ee.scope/3':
            expected.pop('intent')
        if expected not in requirements:
            raise ValueError('block requirement mapping differs from board baseline')
    _fresh_contract(inputs, source, record, scope, files, params['netlist_check'])
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
    _mapping(source, request['requirements'])
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
    from . import native_run
    from .checks import CheckError
    from .native_netlist import read_native_inventory
    work = ctx.work / label
    if work.exists():
        raise CheckError('malformed', 'native export workspace already exists: ' + label)
    work.mkdir()
    intent = native_run.netlist(schematic, '/work/design.xml', timeout=120)
    record = ctx.run_native(intent, work=work, mounts=extra_ro,
                            exhausted='deadline exhausted before ' + label + ' export')
    ctx.evidence(*(label + '/' + name for name in ('execution.json', 'console.log', 'design.xml')))
    if native_run.cut_by_deadline(record, intent):
        raise CheckError('interrupted', 'deadline cut off ' + label + ' export')
    if record['execution'] != 'ok' or record['exit_code'] != 0:
        raise CheckError(record['execution'] if record['execution'] != 'ok' else 'failed', label + ' native export failed')
    if (work / 'design.xml').is_symlink():
        raise CheckError('malformed', label + ' native output is a symlink')
    return read_native_inventory(work / 'design.xml')


def root_membership(source):
    sets=[c['params']['reference_set'] for c in source.get('check_set',[]) if c['kind']=='parts_subset' and 'reference_set' in c['params']]
    if len(sets)!=1:
        raise ValueError('root composition requires one captured exact membership contract')
    from .parts_subset import reference_set
    return reference_set(sets[0])


def root_coverage(inputs, scope, actual):
    """No root physical component is hidden outside the selected block union."""
    rows=[]
    for path in scope.get('block_instances',[]):
        record,source=_instance(inputs,path)
        if record['instance_path']!='/':
            raise ValueError('root role scope cannot mix child occurrences')
        rows += root_membership(source)
    keys=[(r['sheet'],r['reference']) for r in rows]
    if not rows or len(set(keys))!=len(keys):
        raise ValueError('root role reference sets must be disjoint')
    observed={(c['sheet'],ref):sorted(c['uuid'].split()) for ref,c in actual['components'].items()}
    if observed != {(r['sheet'],r['reference']):r['uuids'] for r in rows}:
        raise ValueError('root role union differs from the complete native physical inventory')


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
            if record['instance_path']=='/':
                root_coverage(ctx.inputs, ctx.scope, actual)
            component_changes, net_changes = compare(selected, actual, occurrence, record['bindings'],
                reference_set=root_membership(block) if record['instance_path']=='/' else None)
        ports = hierarchy_ports((ctx.inputs / record['sheet']).read_bytes())
        if record['instance_path'] == '/':
            if ports:
                raise ValueError('root composition cannot contain hierarchical boundary ports')
            boundary = []
        else:
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
    _register_check('block_provenance', describe_block_provenance, check_block_provenance,
        early_prerequisites=(_requires.staged_placement_clear,), prerequisites=(_requires.stage_progress,))
