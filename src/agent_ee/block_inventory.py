"""Pinned design block inventory captured as draft agent-ee.block/1 entries.

An ``agent-ee.block-inventory/1`` document names, for one source design and one
full commit, every block's sheet instances and reference sets, its requirements
with sources and its check set. Capture reads only that commit's Git blobs,
derives connectivity from one sandboxed native netlist export of those blobs,
and writes one private block directory per block through
``reference_blocks.capture_block``. Derived contracts restate the pinned design;
they are drafts for owner approval, never evidence that a block is verified.
"""
import argparse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .reference_blocks import (_git, _hash, _path, _read, _text, capture_block, hierarchy_ports,
                               sheet_symbols, validate_block)
from .storage import canonical, digest, identity, rename_noreplace, write_json
from .symbol_library import children, field, parse_sexpr

SCHEMA = 'agent-ee.block-inventory/1'
CONNECTIVITY = 'agent-ee.block-connectivity/1'
ADAPTATION_FIXTURE = 'agent-ee.block-adaptation-fixture/1'
CHECK_SET = [dict(id='erc', kind='erc', params={}), dict(id='netlist', kind='netlist', params={}),
             dict(id='interface', kind='hardware_interface',
                  params=dict(contract='interface.json', netlist_check='netlist'))]
GENERATED = ('Net-(', 'unconnected-(')
STOCK_ROOT = '/usr/share/kicad'
NATIVE_LIMIT_S = 600


def _exact(value, fields, optional=()):
    if not isinstance(value, dict) or set(fields) - value.keys() or value.keys() - set(fields) - set(optional):
        raise ValueError('expected fields: ' + ', '.join(fields))


def _texts(value, empty=False):
    if not isinstance(value, list) or (not value and not empty) or len(value) > 10000:
        raise ValueError('expected bounded text list')
    for item in value:
        _text(item)
    if len(set(value)) != len(value):
        raise ValueError('duplicate text entry')


def validate_inventory(document):
    """Structural validation; references and documents are resolved at capture."""
    _exact(document, ('schema', 'design', 'documents', 'unassigned', 'blocks'))
    if document['schema'] != SCHEMA:
        raise ValueError('unknown block inventory schema')
    design = document['design']
    _exact(design, ('name', 'commit', 'root', 'license', 'plan'), ('external_libraries', 'cite_documents'))
    for key in ('name', 'license'):
        _text(design[key])
    _hash(design['commit'], 40)
    for name, commit in design.get('external_libraries', {}).items():
        if re.fullmatch('[A-Za-z0-9_.-]+', name) is None or name in ('.', '..'):
            raise ValueError('external library names are plain directory names')
        _hash(commit, 40)
    _path(design['root'])
    documents = document['documents']
    if not isinstance(documents, dict) or design['plan'] not in documents:
        raise ValueError('inventory documents must include the plan')
    for name, path in documents.items():
        _text(name)
        _path(path)
    for entry in document['unassigned']:
        _exact(entry, ('instance', 'references', 'reason'))
        _text(entry['instance'])
        if entry['references'] != 'all':
            _texts(entry['references'])
        _text(entry['reason'])
    if not isinstance(document['blocks'], list) or not document['blocks']:
        raise ValueError('inventory needs blocks')
    ids = set()
    for block in document['blocks']:
        _exact(block, ('id', 'role', 'title', 'origin', 'upstream_license', 'instances', 'boundary', 'pending'))
        for key in ('id', 'role', 'title', 'origin', 'upstream_license'):
            _text(block[key])
        if re.fullmatch('[a-z0-9][a-z0-9-]*', block['id']) is None or block['id'] in ids:
            raise ValueError('block ids are unique lowercase slugs')
        ids.add(block['id'])
        if not isinstance(block['instances'], list) or not block['instances']:
            raise ValueError('block needs instances')
        for instance in block['instances']:
            _exact(instance, ('path', 'references'))
            _text(instance['path'])
            if instance['references'] != 'all':
                _texts(instance['references'])
        _source(block['boundary'], documents)
        requirement_ids = {block['id'] + '-native', block['id'] + '-interface'}
        for req in block['pending']:
            _exact(req, ('id', 'statement', 'document', 'locator', 'conditions', 'blocked_by'))
            for key in ('id', 'statement', 'locator', 'blocked_by'):
                _text(req[key])
            _texts(req['conditions'], empty=True)
            if req['document'] not in documents or req['id'] in requirement_ids:
                raise ValueError('pending requirement needs a known document and unique id')
            requirement_ids.add(req['id'])
    return document


def _source(value, documents):
    _exact(value, ('document', 'locator'))
    _text(value['locator'])
    if value['document'] not in documents:
        raise ValueError('unknown inventory document')


def _blob(repo, commit, path):
    entry = _git(repo, 'ls-tree', '-z', commit, '--', ':(literal)' + path).split(b'\x00')
    if len(entry) != 2 or not entry[0].startswith((b'100644 blob ', b'100755 blob ')):
        return None
    return _git(repo, 'show', commit + ':' + path)


def _tree(repo, commit):
    return [p.decode() for p in _git(repo, 'ls-tree', '-r', '-z', '--name-only', commit).split(b'\x00') if p]


def _hierarchy(blobs, root):
    """Name path -> (native UUID path, pinned sheet file), from the pinned blobs."""
    result = {'/': ('/', root)}

    def walk(path, names, uuids, stack):
        if path not in blobs or path in stack:
            raise ValueError('sheet hierarchy is missing or cyclic at ' + path)
        for sheet in children(parse_sexpr(blobs[path]), 'sheet'):
            props = {p[1]: p[2] for p in children(sheet, 'property') if len(p) > 2}
            child = posixpath.normpath(posixpath.join(posixpath.dirname(path), props['Sheetfile']))
            uuid, = field(sheet, 'uuid', 1)
            name, native = names + props['Sheetname'] + '/', uuids + uuid + '/'
            if name in result:
                raise ValueError('duplicate sheet instance name: ' + name)
            result[name] = (native, child)
            walk(child, name, native, stack | {path})
    walk(root, '/', '/', frozenset())
    return result


def native_export(repo, commit, root, scratch):
    """Export the pinned schematics' native XML netlist inside the network-less sandbox."""
    from . import native_run
    inputs, work = Path(scratch) / 'inputs', Path(scratch) / 'work'
    blobs = {}
    for path in _tree(repo, commit):
        if path.endswith(('.kicad_sch', '.kicad_pro')):
            data = _blob(repo, commit, path)
            if data is None:
                raise ValueError('native design file is not a regular committed file: ' + path)
            blobs[path] = data
            target = inputs / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    work.mkdir()
    record = dict(native_run.run(native_run.netlist('/candidate/' + root, '/work/native.xml', timeout=NATIVE_LIMIT_S),
                                 inputs=inputs, work=work))
    if record['execution'] != 'ok' or not (work / 'native.xml').is_file():
        raise ValueError('native netlist export failed: ' + record['execution'] + ' '
                         + (work / 'console.log').read_text(errors='replace')[-2000:])
    probe = Path(scratch) / 'version'
    probe.mkdir()
    if dict(native_run.run(native_run.Intent('kicad-cli', ['kicad-cli', 'version'], timeout=60), inputs=inputs, work=probe))['execution'] != 'ok':
        raise ValueError('kicad-cli version probe failed')
    tool = (probe / 'stdout.log').read_text().strip()
    console = (work / 'console.log').read_text(errors='replace').strip()
    return blobs, work / 'native.xml', 'kicad-cli ' + tool, console


def _lib_tables(repo, commit):
    tables = {}
    for kind, path in (('footprint', 'fp-lib-table'), ('symbol', 'sym-lib-table')):
        data = _blob(repo, commit, path)
        rows = {}
        if data is not None:
            for lib in children(parse_sexpr(data), 'lib'):
                rows[field(lib, 'name', 1)[0]] = field(lib, 'uri', 1)[0]
        tables[kind] = rows
    return tables


def _library(kind, lib_id, tables, repo, commit, stock_root, tool, externals):
    """Resolve one library identity to a pinned file, a stock file or nothing.

    ``externals`` maps a sibling directory named by ``${KIPRJMOD}/../NAME/`` to a
    local repository and pinned commit; such files are hashed, never copied.
    """
    nickname, member = lib_id.split(':')
    uri = tables[kind].get(nickname)
    if kind == 'footprint':
        stock = f'footprints/{nickname}.pretty/{member}.kicad_mod'
        suffix = '/' + member + '.kicad_mod'
    else:
        stock = f'symbols/{nickname}.kicad_sym'
        suffix = ''
    if uri is not None and uri.startswith('${KIPRJMOD}/') and not uri.startswith('${KIPRJMOD}/../'):
        path = posixpath.normpath(uri[len('${KIPRJMOD}/'):] + suffix)
        data = _blob(repo, commit, path)
        if data is not None:
            return dict(kind=kind, lib_id=lib_id, origin='source', path=path, sha256=hashlib.sha256(data).hexdigest(),
                        detail='Project library ' + uri + ' at the pinned commit'), {path: path}
        return dict(kind=kind, lib_id=lib_id, origin='unresolved', path=None, sha256=None,
                    detail=f'Project library {uri} has no committed {path}'), {}
    if uri is None or uri.startswith(STOCK_ROOT + '/') or uri.startswith('${KICAD10_'):
        path = Path(stock_root) / stock
        if path.is_file() and not path.is_symlink():
            return dict(kind=kind, lib_id=lib_id, origin='kicad-stock', path=stock, sha256=digest(path),
                        detail=f'KiCad stock library at {stock_root}, {tool}'), {}
        return dict(kind=kind, lib_id=lib_id, origin='unresolved', path=None, sha256=None,
                    detail=f'No project table row and no stock {stock} under {stock_root}'), {}
    sibling = uri[len('${KIPRJMOD}/../'):].split('/', 1) if uri.startswith('${KIPRJMOD}/../') else []
    if len(sibling) == 2 and sibling[0] in externals:
        external_repo, external_commit = externals[sibling[0]]
        path = posixpath.normpath(sibling[1] + suffix)
        data = _blob(external_repo, external_commit, path)
        if data is not None:
            return dict(kind=kind, lib_id=lib_id, origin='external', path=sibling[0] + '/' + path,
                        sha256=hashlib.sha256(data).hexdigest(),
                        detail=f'External library {sibling[0]} at {external_commit}; hashed, not captured'), {}
    return dict(kind=kind, lib_id=lib_id, origin='unresolved', path=None, sha256=None,
                detail='Library outside the pinned repository: ' + uri), {}


def _native_rounded(node):
    """Round placement atoms to KiCad's 100 nm schematic unit, as its reader does on load.

    Some generated sheets carry binary-float noise such as ``126.99999999999999``;
    the strict geometry reader refuses those, while eeschema rounds them half away from zero.
    """
    if not isinstance(node, list):
        return node
    if node and node[0] in ('at', 'xy', 'size'):
        rounded = [node[0]]
        for atom in node[1:]:
            try:
                value = Decimal(atom).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
                rounded.append(format(value.normalize(), 'f') if value else '0')
            except (InvalidOperation, TypeError):
                rounded.append(atom)
        return rounded
    return [_native_rounded(child) for child in node]


def _conductors(blob, uuid_path, inventory, refs):
    """Native nets reached by each hierarchical label through exact source geometry.

    As in hardware_interface.label_connection, wires join only at shared endpoints
    or a junction on both; labels and junctions attach anywhere on a wire, pins
    only at its endpoints (a pin crossing a wire's middle is not connected).
    Coincident points and same-kind labels with one name also join. The caller
    requires one native net per label, so an over-joined conductor is refused.
    """
    from .hardware_interface import sheet_items, within
    sheet = _native_rounded(parse_sexpr(blob))
    # Pre-instance (20211123) sheets omit (unit 1) on single-unit symbols; eeschema defaults it.
    sheet = [sheet[0]] + [node + [['unit', '1']] if isinstance(node, list) and node[:1] == ['symbol']
                          and not children(node, 'unit') else node for node in sheet[1:]]
    items, _ = sheet_items(sheet)
    occurrence = {uuid: ref for ref, entry in sheet_symbols(blob, uuid_path).items() for uuid in entry['uuids']}
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(i, j):
        parent[find(i)] = find(j)
    wires = [i for i, item in enumerate(items) if item['kind'] in ('wire', 'bus')]
    points = [i for i, item in enumerate(items) if len(item['shape']) == 1]
    at, names = {}, {}
    for i in points:
        at.setdefault(items[i]['shape'][0], []).append(i)
        if items[i].get('name') is not None:
            names.setdefault((items[i]['kind'], items[i]['name']), []).append(i)
    for group in (*at.values(), *names.values()):
        for i in group[1:]:
            join(group[0], i)
    junctions = [items[i]['shape'][0] for i in points if items[i]['kind'] == 'junction']
    for w in wires:
        segment = items[w]['shape']
        for i in points:
            point = items[i]['shape'][0]
            if point in segment or (items[i]['kind'] != 'pin' and within(point, *segment, 0)):
                join(w, i)
        for v in wires:
            if v > w and (set(segment) & set(items[v]['shape']) or any(
                    within(j, *segment, 0) and within(j, *items[v]['shape'], 0) for j in junctions)):
                join(w, v)
    result = {}
    for i in points:
        if items[i]['kind'] != 'hierarchical_label':
            continue
        root = find(i)
        codes = set()
        for j, pin in enumerate(items):
            if pin['kind'] == 'pin' and find(j) == root and occurrence.get(pin['uuid']) in refs:
                code = inventory['memberships'].get((occurrence[pin['uuid']], pin['number']))
                if code is not None:
                    codes.add(code)
        result.setdefault(items[i]['name'], set()).update(inventory['nets'][c] for c in codes)
    return result


def _boundary_ports(blob, instance, inventory, members):
    ports = hierarchy_ports(blob)
    nets = set(inventory['nets'].values())
    observed = _conductors(blob, instance['uuid_path'], inventory, members)
    result = []
    for name, direction in sorted(ports.items()):
        candidates = observed.get(name, set())
        if len(candidates) > 1:
            raise ValueError(f'hierarchical label {name} joins several native nets: {sorted(candidates)}')
        if not candidates and instance['path'] + name in nets:
            candidates = {instance['path'] + name}
        if not candidates:
            raise ValueError(f'hierarchical label {instance["path"]}{name} resolves to no native net')
        # Electrical domain and current are owner declarations; acquisition leaves them unknown.
        result.append(dict(name=name, net_name=candidates.pop(), direction=direction,
                           voltage_domain=None, current_a=None))
    return result


def _net_key(code, inventory, members):
    name = inventory['nets'][code]
    terminals = sorted(f'{r}.{p}' for (r, p), c in inventory['memberships'].items() if c == code and r in members)
    if name.startswith(GENERATED[1]) and len(terminals) == 1:
        return None
    return name if not name.startswith(GENERATED) else 'anonymous:' + terminals[0]


def _block_request(inventory, block, context):
    """Assemble one draft block request plus its generated supplements."""
    commit, docs = inventory['design']['commit'], inventory['documents']
    native, components, hierarchy = context['native'], context['components'], context['hierarchy']
    instances, members = [], {}
    for item in block['instances']:
        if item['path'] not in hierarchy:
            raise ValueError('unknown sheet instance: ' + item['path'])
        uuid_path, sheet = hierarchy[item['path']]
        local = sorted(c['reference'] for c in components.values() if c['sheet'] == uuid_path)
        refs = local if item['references'] == 'all' else sorted(item['references'])
        if not refs or set(refs) - set(local):
            raise ValueError(f'references outside {item["path"]}: {sorted(set(refs) - set(local))}')
        instances.append(dict(path=item['path'], uuid_path=uuid_path, sheet=sheet, references=refs))
        members.update({ref: instances[-1] for ref in refs})
    paths = {i['sheet']: i['sheet'] for i in instances}
    libraries, symbols = [], {}
    for instance in instances:
        found = sheet_symbols(context['blobs'][instance['sheet']], instance['uuid_path'])
        for ref in instance['references']:
            if ref not in found:
                raise ValueError('native component has no captured source symbol: ' + ref)
            symbols[ref] = found[ref]
    used = sorted({('symbol', s['lib_id']) for s in symbols.values()} |
                  {('footprint', s['footprint']) for s in symbols.values() if s['footprint']})
    for kind, lib_id in used:
        entry, extra = _library(kind, lib_id, context['tables'], context['repo'], commit,
                                context['stock_root'], context['tool'], context['externals'])
        libraries.append(entry)
        paths.update(extra)
    # Planning documents are cited by pinned identity, never copied into a block.
    source = lambda document, locator: dict(context['citations'][docs[document]], locator=locator)
    plan = inventory['design']['plan']
    interface_source = source(block['boundary']['document'], block['boundary']['locator'])
    requirements = [
        dict(id=block['id'] + '-native', statement=(
            'The captured block sheets load natively, pass ERC and export a native netlist '
            'in which every reference-set component is present.'),
            source=source(plan, 'What "verified block" means, items 1 and 4'),
            conditions=['KiCad ' + context['tool'].split()[-1] + ' native schematic reader'],
            checks=['erc', 'netlist']),
        dict(id=block['id'] + '-interface', statement=(
            'Every reference-set terminal on a net that leaves the block stays on the named native '
            'net, and every reference-set component keeps its populated state and value, as in '
            'the captured interface contract.'),
            source=interface_source, conditions=['Pinned source commit ' + commit], checks=['interface'])]
    pending = [dict(id=r['id'], statement=r['statement'], source=source(r['document'], r['locator']),
                    conditions=r['conditions'], blocked_by=r['blocked_by']) for r in block['pending']]
    primary = instances[0]
    # A label is a fact of the whole sheet occurrence, not of the reference subset.
    ports = _boundary_ports(context['blobs'][primary['sheet']], primary, native,
                            {c['reference'] for c in components.values() if c['sheet'] == primary['uuid_path']})
    record = dict(schema='agent-ee.block/1', id=inventory['design']['name'] + '/' + block['id'],
                  version='draft-' + commit[:12], role=block['role'],
                  source=dict(repository='', commit=commit, paths=paths,
                              license=inventory['design']['license'] + ' Origin: ' + block['origin'],
                              upstream_origin=block['origin'],
                              upstream_license=block['upstream_license']),
                  sheet=primary['sheet'], parts_manifest='parts.json', boundary_ports=ports,
                  requirements=requirements, files={}, runs=[], instances=instances,
                  interface='interface.json', connectivity='connectivity.json',
                  check_set=json.loads(json.dumps(CHECK_SET)), pending_requirements=pending,
                  libraries=libraries)
    supplements = {
        'parts.json': canonical(_parts(members, components, symbols, context)),
        'connectivity.json': canonical(_projection(members, native, components, context)),
        'interface.json': canonical(_contract(members, native, block['id'] + '-interface', interface_source)),
    }
    return record, supplements, set(members)


def _parts(members, components, symbols, context):
    parts = []
    for ref in sorted(members):
        props = components[ref]['properties']
        lcsc = props.get('LCSC') or None
        catalog = context['catalog']
        parts.append(dict(instance=members[ref]['uuid_path'], reference=ref, lib_id=symbols[ref]['lib_id'],
                          value=components[ref]['value'], footprint=components[ref]['footprint'],
                          lcsc=lcsc, mpn=props.get('Manufacturer_Part_Number') or None,
                          manufacturer=props.get('Manufacturer_Name') or None,
                          populated=components[ref]['populated'],
                          catalog=None if catalog is None else ('present' if lcsc in catalog['lcsc'] else 'absent')))
    catalog = context['catalog']
    return dict(schema='agent-ee.block-parts/1',
                source=dict(commit=context['commit'], exporter=context['tool'],
                            fields=dict(lcsc='LCSC', mpn='Manufacturer_Part_Number',
                                        manufacturer='Manufacturer_Name')),
                catalog=None if catalog is None else catalog['identity'], parts=parts)


def _projection(members, native, components, context):
    """Per-terminal native conductor identity; generated names become anonymous groups."""
    result, nets = {}, {}
    for ref in sorted(members):
        key = members[ref]['uuid_path'], ref
        pins = {}
        for pin in native['components'][key]['pins']:
            code = native['memberships'].get((ref, pin))
            net = None if code is None or (ref, pin) in native['disconnected'] else _net_key(code, native, members)
            pins[pin] = net
            if net is not None and net not in nets:
                outside = sum(1 for (r, _), c in native['memberships'].items() if c == code and r not in members)
                nets[net] = dict(native_name=native['nets'][code], boundary=outside > 0)
        result[ref] = dict(instance=members[ref]['uuid_path'], symbol=native['components'][key]['symbol'],
                           value=components[ref]['value'], footprint=components[ref]['footprint'],
                           populated=components[ref]['populated'], pins=pins)
    return dict(schema='agent-ee.block-connectivity/1', commit=context['commit'], exporter=context['tool'],
                components=result, nets=dict(sorted(nets.items())))


def _contract(members, native, requirement, source):
    components, terminals, invariants = {}, {}, []
    for ref in sorted(members):
        key = members[ref]['uuid_path'], ref
        comp = native['components'][key]
        components[ref] = dict(locations=[dict(sheet=key[0], reference=ref)], symbols=[comp['symbol']])
        invariants.append(dict(id=ref + ':present', requirement=requirement, source=source,
                               kind='component', component=ref, populated=comp['populated']))
        if comp['value'].strip():
            invariants.append(dict(id=ref + ':value', requirement=requirement, source=source,
                                   kind='value', component=ref, equals=comp['value']))
        for pin in comp['pins']:
            code = native['memberships'].get((ref, pin))
            if code is None or (ref, pin) in native['disconnected']:
                continue
            name = native['nets'][code]
            leaves = any(c == code and r not in members for (r, _), c in native['memberships'].items())
            if leaves and not name.startswith(GENERATED):
                terminals[f'{ref}.{pin}'] = dict(component=ref, pin=pin)
                invariants.append(dict(id=f'{ref}.{pin}:net', requirement=requirement, source=source,
                                       kind='named_net', terminal=f'{ref}.{pin}', names=[name]))
    from .hardware_interface import evaluate, validate_contract
    contract = validate_contract(dict(schema='agent-ee.interface/1', components=components,
                                      terminals=terminals, allowed_edits=[], invariants=invariants))
    # The derived draft must hold on its own export; a failure is a derivation defect.
    failed = [r['id'] for r in evaluate(contract, dict(native, sources={}, unresolved={}))
              if r['status'] != 'satisfied']
    if failed:
        raise ValueError('derived interface contract does not hold on its export: ' + ', '.join(failed[:5]))
    return contract


def _load_catalog(directory, manifest_sha256):
    if directory is None:
        return None
    from .catalog import load_catalog
    loaded = load_catalog(directory, manifest_sha256)
    manifest = loaded['manifest']
    return dict(lcsc={row['lcsc'] for row in loaded['rows']},
                identity=dict(manifest_sha256=manifest_sha256, database_sha256=manifest['database']['sha256'],
                              snapshot_date=manifest['snapshot_date']))


def _pinned(repository, commit):
    if _git(repository, 'rev-parse', '--verify', commit + '^{commit}').decode().strip() != commit:
        raise ValueError('inventory commit must be a full exact commit')


def _citations(repository, commit, documents, label):
    """Pinned-external citation identity of each inventory document (see reference_blocks)."""
    citations = {}
    for path in sorted(set(documents.values())):
        data = _blob(repository, commit, path)
        if data is None:
            raise ValueError('inventory document is not committed: ' + path)
        citations[path] = dict(kind='pinned-external', repository=label, commit=commit, path=path,
                               blob=hashlib.sha1(b'blob %d\x00' % len(data) + data).hexdigest(),
                               sha256=hashlib.sha256(data).hexdigest())
    return citations


def _source_files(repository, commit, blobs, requests, cited):
    """Pinned source files exported, captured or cited: SHA-256, Git blob id and whether a block copies it."""
    captured = {path for _, record, _ in requests for path in record['source']['paths'].values()}
    files = {}
    for path in sorted(set(blobs) | captured | set(cited)):
        data = blobs[path] if path in blobs else _blob(repository, commit, path)
        files[path] = dict(sha256=hashlib.sha256(data).hexdigest(),
                           git_blob=hashlib.sha1(b'blob %d\x00' % len(data) + data).hexdigest(),
                           captured=path in captured)
    return files


def capture_inventory(inventory, repository, destination, *, catalog=None, catalog_manifest_sha256=None,
                      stock_root=STOCK_ROOT, library_repos=None, repository_label=None):
    """Capture every inventory block from its pinned commit into a new private directory.

    ``library_repos`` maps declared ``external_libraries`` names to local repositories;
    an undeclared or absent one leaves its libraries unresolved. ``repository_label``
    is recorded instead of the local repository path (see ``capture_block``).
    """
    validate_inventory(inventory)
    commit = inventory['design']['commit']
    _pinned(repository, commit)
    externals = {}
    declared = inventory['design'].get('external_libraries', {})
    for name, path in (library_repos or {}).items():
        if name not in declared:
            raise ValueError('library repository is not declared by the inventory: ' + name)
        _pinned(path, declared[name])
        externals[name] = (path, declared[name])
    target = Path(destination)
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError('capture destination has a symlink ancestor')
    label = str(Path(repository).resolve()) if repository_label is None else repository_label
    citations = _citations(repository, commit, inventory['documents'], label)
    from .hardware_interface import read_inventory
    from .native_netlist import read_components
    with tempfile.TemporaryDirectory(prefix='agent-ee-block-inventory-') as scratch:
        blobs, xml, tool, console = native_export(repository, commit, inventory['design']['root'], scratch)
        native = read_inventory(xml)
        components = read_components(xml)
    hierarchy = _hierarchy(blobs, inventory['design']['root'])
    context = dict(repo=repository, commit=commit, blobs=blobs, native=native, components=components,
                   hierarchy=hierarchy, tables=_lib_tables(repository, commit), stock_root=str(stock_root),
                   tool=tool, catalog=_load_catalog(catalog, catalog_manifest_sha256), externals=externals,
                   citations=citations)
    exported = {c['sheet'] for c in components.values()}
    if not exported <= {uuid for uuid, _ in hierarchy.values()}:
        raise ValueError('native export hierarchy differs from the pinned sheet hierarchy')
    requests, assigned = [], {}
    for block in inventory['blocks']:
        record, supplements, refs = _block_request(inventory, block, context)
        for ref in refs:
            if ref in assigned:
                raise ValueError(f'{ref} is assigned to both {assigned[ref]} and {block["id"]}')
            assigned[ref] = block['id']
        requests.append((block['id'], record, supplements))
    for entry in inventory['unassigned']:
        uuid_path = hierarchy.get(entry['instance'], (None,))[0]
        refs = entry['references']
        if refs == 'all':
            # A whole sheet occurrence outside every captured block, for a single-block inventory.
            if uuid_path is None:
                raise ValueError('unknown unassigned instance: ' + entry['instance'])
            refs = sorted(r for r, c in components.items() if c['sheet'] == uuid_path)
        for ref in refs:
            if ref in assigned or components.get(ref, {}).get('sheet') != uuid_path:
                raise ValueError('unassigned reference is assigned or outside its instance: ' + ref)
            assigned[ref] = None
    missing = sorted(set(components) - set(assigned))
    if missing:
        raise ValueError('native components without a block or explicit reason: ' + ', '.join(missing))
    if target.exists():
        raise FileExistsError(str(target))
    # Stage beside the destination; a refusal part way through publishes nothing.
    stage = Path(tempfile.mkdtemp(prefix='.' + target.name + '.', dir=target.parent))
    try:
        blocks = []
        for block_id, record, supplements in requests:
            out = stage / block_id
            report = capture_block(record, repository, commit, out, supplements=supplements,
                                   repository_label=repository_label)
            blocks.append(dict(id=record['id'], directory=block_id, status=report['status'],
                               block_sha256=digest(out / 'block.json'),
                               contract_sha256=digest(out / 'block-contract.json'),
                               pending_requirements=report['pending_requirements'],
                               unresolved_libraries=report['unresolved_libraries'],
                               undeclared_ports=report['undeclared_ports']))
        summary = dict(schema='agent-ee.block-inventory-report/1', design=inventory['design']['name'],
                       commit=commit, inventory_sha256=identity(inventory), exporter=tool,
                       exporter_console=console, components=len(components), blocks=blocks,
                       source_files=_source_files(repository, commit, blobs, requests, citations),
                       unassigned=inventory['unassigned'],
                       limitations=['Draft entries: derived contracts restate the pinned design and need owner '
                                    'approval; no block is verified without a complete run and review.',
                                    'Captured design files are the pinned source blobs; publishing them follows '
                                    'the source design licence statement and the owner decision that covers it.',
                                    'Planning documents are cited by pinned commit, path, Git blob and SHA-256 '
                                    '(source_files captured: false) and are never copied into a block.'])
        write_json(stage / 'inventory-report.json', summary)
        rename_noreplace(stage, target)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return summary


def adapt_connectivity(projection, parameters):
    """Apply copy parameters to a block projection: reference offset, renames, straps.

    ``parameters`` has ``reference_offset`` (integer added to every designator
    number), ``renames`` (ordered ``[old, new]`` substring pairs applied to named
    nets) and ``straps`` (``{reference, pin, net}`` on the adapted references,
    with ``net`` null for an open pin). Anonymous groups are compared by membership.
    """
    _exact(parameters, ('reference_offset', 'renames', 'straps'))
    offset = parameters['reference_offset']
    if type(offset) is not int:
        raise ValueError('reference offset must be an integer')
    if not isinstance(parameters['renames'], list) or any(
            not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(t, str) and t for t in pair)
            for pair in parameters['renames']):
        raise ValueError('renames must be [old, new] text pairs')
    if not isinstance(parameters['straps'], list):
        raise ValueError('straps must be a list')

    def ref(value):
        match = re.fullmatch('([A-Za-z_]+)([0-9]+)', value)
        if match is None:
            raise ValueError('reference has no designator number: ' + value)
        return match[1] + str(int(match[2]) + offset)

    def name(value):
        for old, new in parameters['renames']:
            value = value.replace(old, new)
        return value
    groups = {}
    for r, comp in projection['components'].items():
        for pin, net in comp['pins'].items():
            if net is not None and net.startswith('anonymous:'):
                groups.setdefault(net, set()).add((ref(r), pin))
    adapted = {}
    for r, comp in projection['components'].items():
        pins = {}
        for pin, net in comp['pins'].items():
            if net is None:
                pins[pin] = ('disconnected',)
            elif net.startswith('anonymous:'):
                pins[pin] = ('anonymous', frozenset(groups[net]))
            else:
                pins[pin] = ('named', name(net))
        adapted[ref(r)] = dict(symbol=comp['symbol'], value=comp['value'], footprint=comp['footprint'],
                               populated=comp['populated'], pins=pins)
    for strap in parameters['straps']:
        _exact(strap, ('reference', 'pin', 'net'))
        if strap['reference'] not in adapted or strap['pin'] not in adapted[strap['reference']]['pins']:
            raise ValueError('strap names an unknown adapted terminal')
        adapted[strap['reference']]['pins'][strap['pin']] = (
            ('disconnected',) if strap['net'] is None else ('named', strap['net']))
    return adapted


def validate_adaptation_fixture(document):
    """An expected adaptation: parameters plus the differences seen without straps."""
    _exact(document, ('schema', 'design', 'source', 'target', 'description', 'parameters',
                      'expected_without_straps'))
    if document['schema'] != ADAPTATION_FIXTURE:
        raise ValueError('unsupported adaptation fixture schema')
    for key in ('design', 'source', 'target', 'description'):
        _text(document[key])
    if document['source'] == document['target']:
        raise ValueError('adaptation source and target must differ')
    _exact(document['parameters'], ('reference_offset', 'renames', 'straps'))
    if not isinstance(document['expected_without_straps'], list):
        raise ValueError('expected differences must be a list')
    for diff in document['expected_without_straps']:
        _exact(diff, ('reference', 'field', 'adapted', 'captured'), ('pin',))


def compare_connectivity(adapted, projection):
    """Differences between an adapted copy and a captured projection, per terminal."""
    target = adapt_connectivity(projection, dict(reference_offset=0, renames=[], straps=[]))
    diffs = []
    for r in sorted(set(adapted) | set(target)):
        if r not in adapted or r not in target:
            diffs.append(dict(reference=r, field='component', adapted=r in adapted, captured=r in target))
            continue
        for key in ('symbol', 'value', 'footprint', 'populated'):
            if adapted[r][key] != target[r][key]:
                diffs.append(dict(reference=r, field=key, adapted=adapted[r][key], captured=target[r][key]))
        pins = adapted[r]['pins'], target[r]['pins']
        for pin in sorted(set(pins[0]) | set(pins[1]), key=lambda p: (len(p), p)):
            a, b = pins[0].get(pin), pins[1].get(pin)
            if a != b:
                show = lambda v: None if v is None else (v[0] if v[0] != 'named' else 'named:' + v[1])
                diffs.append(dict(reference=r, field='pin', pin=pin, adapted=show(a), captured=show(b)))
    return diffs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    capture = commands.add_parser('capture')
    capture.add_argument('inventory')
    capture.add_argument('--repo', required=True)
    capture.add_argument('--out', required=True)
    capture.add_argument('--catalog', help='captured agent-ee.catalog/1 snapshot directory')
    capture.add_argument('--catalog-manifest-sha256')
    capture.add_argument('--stock-root', default=STOCK_ROOT)
    capture.add_argument('--library-repo', action='append', default=[], metavar='NAME=PATH',
                         help='local repository for a declared external library')
    capture.add_argument('--repository-label',
                         help='record this name instead of the local repository path')
    adapt = commands.add_parser('adapt')
    adapt.add_argument('source', help='captured source block directory')
    adapt.add_argument('target', help='captured target block directory')
    adapt.add_argument('--parameters', required=True, help='adaptation parameters JSON')
    args = parser.parse_args(argv)
    try:
        if args.command == 'capture':
            if (args.catalog is None) != (args.catalog_manifest_sha256 is None):
                raise ValueError('catalog snapshot and its manifest SHA-256 go together')
            repos = {}
            for value in args.library_repo:
                name, sep, path = value.partition('=')
                if not sep or not name or not path or name in repos:
                    raise ValueError('library repository must be unique NAME=PATH')
                repos[name] = path
            report = capture_inventory(_read(args.inventory), args.repo, args.out, catalog=args.catalog,
                                       catalog_manifest_sha256=args.catalog_manifest_sha256,
                                       stock_root=args.stock_root, library_repos=repos,
                                       repository_label=args.repository_label)
            print(json.dumps(report, sort_keys=True))
            return 0
        projections = []
        for directory in (args.source, args.target):
            validate_block(directory)
            record = _read(Path(directory) / 'block.json')
            projections.append(_read(Path(directory) / record['connectivity']))
        diffs = compare_connectivity(adapt_connectivity(projections[0], _read(args.parameters)), projections[1])
        print(json.dumps(dict(schema='agent-ee.block-adaptation-diff/1', differences=diffs), sort_keys=True))
        return 0 if not diffs else 1
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps(dict(schema='agent-ee.block-inventory-report/1', status='refused', error=str(exc))))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
