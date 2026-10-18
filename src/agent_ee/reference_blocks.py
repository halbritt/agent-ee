"""Private offline block capture and provenance, using existing run authority."""
import argparse
import copy
import hashlib
import json
import math
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import subprocess
import traceback

from .storage import canonical, digest, write_json
from .symbol_library import children, field, parse_sexpr
from .parts_manifest import PREDICATE, validate_manifest


def _object(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('expected fields: ' + ', '.join(required))


def _text(value):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError('expected nonempty text')


def _path(value):
    _text(value)
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value or '\\' in value or value == '.':
        raise ValueError('unsafe block path: ' + value)
    if path.parts[0] in ('block.json', 'validation.json'):
        raise ValueError('reserved block path')
    return path


def _hash(value, length=64):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is None:
        raise ValueError('invalid content identity')


def _list(value):
    if not isinstance(value, list) or len(value) > 10000:
        raise ValueError('expected bounded list')


def _read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    data = Path(path).read_bytes()
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('block JSON exceeds 8 MiB')
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def hierarchy_ports(data):
    """Read explicit native child-sheet terminals with the shared AGENTEE-39 AST."""
    root = parse_sexpr(data)
    if root[0] != 'kicad_sch':
        raise ValueError('expected native schematic')
    result = {}
    for label in children(root, 'hierarchical_label'):
        if len(label) < 2:
            raise ValueError('missing hierarchical label name')
        name = label[1]
        _text(name)
        direction, = field(label, 'shape', 1)
        # KiCad joins repeated same-name labels into one sheet pin; a repeated
        # name with a different shape has no single native direction.
        if direction not in ('input', 'output', 'bidirectional', 'tri_state', 'passive') or (
                result.get(name, direction) != direction):
            raise ValueError('unsupported or conflicting hierarchical port: ' + name)
        result[name] = direction
    return result


PORT_FIELDS = ('name', 'net_name', 'direction', 'voltage_domain', 'current_a')
DRAFT_PARTS = 'agent-ee.block-parts/1'
LIBRARY_ORIGINS = ('source', 'kicad-stock', 'external', 'unresolved')


def _source(value, files):
    if isinstance(value, dict) and value.get('kind') == 'pinned-external':
        _object(value, ('kind', 'repository', 'commit', 'path', 'blob', 'sha256'), ('locator',))
        _text(value['repository'])
        _hash(value['commit'], 40)
        _hash(value['blob'], 40)
        _hash(value['sha256'])
        _path(value['path'])
        if 'locator' in value:
            _text(value['locator'])
        return
    _object(value, ('path', 'locator'))
    _path(value['path'])
    _text(value['locator'])
    if value['path'] not in files:
        raise ValueError('requirement source is not a captured block file')


def _texts(value):
    _list(value)
    for item in value:
        _text(item)
    if len(set(value)) != len(value):
        raise ValueError('duplicate text entry')


def _optional_structure(record):
    """Fields a multi-instance reference set needs; absent fields keep version-1 meaning."""
    files, paths = record['files'], record['source']['paths']
    for key in ('interface', 'connectivity'):
        if key in record:
            _path(record[key])
            if record[key] not in files or record[key] in paths or PurePosixPath(record[key]).suffix != '.json':
                raise ValueError('missing/invalid generated ' + key)
    if 'instances' in record:
        if 'upstream_origin' not in record['source'] or 'upstream_license' not in record['source']:
            raise ValueError('inventory block source needs upstream_origin and upstream_license')
        _list(record['instances'])
        if not record['instances']:
            raise ValueError('instances must be nonempty')
        seen, refs = set(), set()
        for instance in record['instances']:
            _object(instance, ('path', 'uuid_path', 'sheet', 'references'))
            for key in ('path', 'uuid_path'):
                _text(instance[key])
                if not instance[key].startswith('/') or not instance[key].endswith('/'):
                    raise ValueError('instance paths are native /.../ sheet paths')
            if instance['sheet'] not in files or instance['sheet'] not in paths or not instance['sheet'].endswith('.kicad_sch'):
                raise ValueError('instance sheet lacks pinned source path')
            _texts(instance['references'])
            if (not instance['references'] or instance['uuid_path'] in seen or
                    refs & set(instance['references']) or instance['references'] != sorted(instance['references'])):
                raise ValueError('instance reference sets must be nonempty, sorted and disjoint')
            seen.add(instance['uuid_path'])
            refs.update(instance['references'])
        if record['instances'][0]['sheet'] != record['sheet']:
            raise ValueError('first instance must be the primary block sheet')
    if 'check_set' in record:
        from .capabilities import KINDS
        _list(record['check_set'])
        ids = set()
        for spec in record['check_set']:
            _object(spec, ('id', 'kind', 'params'))
            _text(spec['id'])
            if spec['kind'] not in KINDS or not isinstance(spec['params'], dict) or spec['id'] in ids:
                raise ValueError('check set needs unique ids, registered kinds and params')
            ids.add(spec['id'])
            if spec['kind'] == 'hardware_interface' and spec['params'].get('contract') != record.get('interface'):
                raise ValueError('interface check must name the captured block interface')
        bound = {c for req in record['requirements'] for c in req['checks']}
        if bound != ids:
            raise ValueError('every check-set check must trace to a block requirement')
    ids = {r['id'] for r in record['requirements']}
    if 'pending_requirements' in record:
        _list(record['pending_requirements'])
        for req in record['pending_requirements']:
            _object(req, ('id', 'statement', 'source', 'conditions', 'blocked_by'))
            for key in ('id', 'statement', 'blocked_by'):
                _text(req[key])
            _source(req['source'], files)
            _texts(req['conditions'])
            if req['id'] in ids:
                raise ValueError('duplicate requirement')
            ids.add(req['id'])
    if 'libraries' in record:
        _list(record['libraries'])
        keys = set()
        for lib in record['libraries']:
            _object(lib, ('kind', 'lib_id', 'origin', 'path', 'sha256', 'detail'))
            _text(lib['lib_id'])
            _text(lib['detail'])
            key = (lib['kind'], lib['lib_id'])
            if (lib['kind'] not in ('symbol', 'footprint') or lib['origin'] not in LIBRARY_ORIGINS or
                    lib['lib_id'].count(':') != 1 or not all(lib['lib_id'].split(':')) or key in keys):
                raise ValueError('library entry needs unique kind, exact lib_id and known origin')
            keys.add(key)
            if lib['origin'] == 'unresolved':
                if lib['path'] is not None or lib['sha256'] is not None:
                    raise ValueError('unresolved library cannot claim a file')
            else:
                _path(lib['path'])
                if lib['sha256'] is not None:
                    _hash(lib['sha256'])
                if lib['origin'] == 'source' and (lib['path'] not in paths or lib['sha256'] != files[lib['path']]):
                    raise ValueError('source library file must be captured with its hash')


def _structure(record):
    _object(record, ('schema', 'id', 'version', 'role', 'source', 'sheet', 'parts_manifest',
                     'boundary_ports', 'requirements', 'files', 'runs'),
            ('layout', 'instances', 'interface', 'connectivity', 'check_set', 'pending_requirements',
             'libraries'))
    if record['schema'] != 'agent-ee.block/1':
        raise ValueError('unknown block schema')
    for key in ('id', 'version', 'role'):
        _text(record[key])
    source = record['source']
    _object(source, ('repository', 'commit', 'paths', 'license'), ('upstream_origin', 'upstream_license'))
    if ('upstream_origin' in source) != ('upstream_license' in source):
        raise ValueError('source upstream_origin and upstream_license must be paired')
    for key in ('repository', 'license', *(('upstream_origin', 'upstream_license') if 'upstream_origin' in source else ())):
        _text(source[key])
    _hash(source['commit'], 40)
    files = record['files']
    if not isinstance(files, dict) or not files or len(files) > 10000:
        raise ValueError('missing/bounded file inventory')
    if 'block-contract.json' not in files:
        raise ValueError('missing immutable block contract')
    for path, checksum in files.items():
        _path(path)
        _hash(checksum)
    if not isinstance(source['paths'], dict) or not source['paths']:
        raise ValueError('missing source paths')
    for local, original in source['paths'].items():
        if local not in files:
            raise ValueError('source path outside inventory')
        _path(original)
    for key, suffix in (('sheet', '.kicad_sch'), ('layout', '.kicad_pcb'), ('parts_manifest', '.json')):
        if key in record:
            path = record[key]
            _path(path)
            if path not in files or PurePosixPath(path).suffix != suffix:
                raise ValueError('missing/invalid ' + key)
            if key != 'parts_manifest' and path not in source['paths']:
                raise ValueError('native file lacks pinned source path')
    _list(record['boundary_ports'])
    names = set()
    for port in record['boundary_ports']:
        _object(port, ('name', 'net_name', 'direction', 'voltage_domain', 'current_a'))
        for key in ('name', 'net_name', 'direction'):
            _text(port[key])
        if port['voltage_domain'] is not None:
            _text(port['voltage_domain'])
        current = port['current_a']
        if current is not None and (type(current) not in (float, int) or
                                    (type(current) is float and not math.isfinite(current)) or current < 0):
            raise ValueError('current_a must be finite nonnegative amperes')
        if port['name'] in names:
            raise ValueError('duplicate boundary port: ' + port['name'])
        names.add(port['name'])
    _list(record['requirements'])
    if not record['requirements']:
        raise ValueError('missing block requirements')
    ids = set()
    for req in record['requirements']:
        _object(req, ('id', 'statement', 'checks'), ('source', 'conditions'))
        _text(req['id'])
        _text(req['statement'])
        if 'source' in req:
            _source(req['source'], files)
        if 'conditions' in req:
            _texts(req['conditions'])
        _list(req['checks'])
        for check in req['checks']:
            _text(check)
        if not req['checks'] or len(set(req['checks'])) != len(req['checks']) or req['id'] in ids:
            raise ValueError('duplicate requirement or missing/duplicate checks')
        ids.add(req['id'])
    _list(record['runs'])
    runs = set()
    for ref in record['runs']:
        _object(ref, ('run_id', 'candidate_id', 'baseline_id', 'files'))
        _text(ref['run_id'])
        _hash(ref['candidate_id'])
        _hash(ref['baseline_id'])
        if ref['run_id'] in runs:
            raise ValueError('duplicate run reference')
        runs.add(ref['run_id'])
        if not isinstance(ref['files'], dict) or set(ref['files']) != set(files):
            raise ValueError('run must cover every payload file')
        for path in ref['files'].values():
            _path(path)
        if len(set(ref['files'].values())) != len(files):
            raise ValueError('run file mappings must be one-to-one')
    _optional_structure(record)


def block_contract(record):
    """Immutable descriptive bytes, avoiding a circular run/manifest self-hash."""
    return {'schema': 'agent-ee.block-contract/1', **{
        key: value for key, value in record.items() if key not in ('schema', 'files', 'runs')}}


def _payload(root, record):
    actual = {}
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('block contains symlink')
        if path.is_file() and path != root / 'block.json':
            actual[path.relative_to(root).as_posix()] = digest(path)
    if actual != record['files']:
        raise ValueError('block file inventory/hash differs')
    if _read(root / 'block-contract.json') != block_contract(record):
        raise ValueError('block contract differs from captured metadata')
    ports = hierarchy_ports((root / record['sheet']).read_bytes())
    declared = {p['name']: p['direction'] for p in record['boundary_ports']}
    differences = [f'{name!r} (declared={declared.get(name)!r}, native={ports.get(name)!r})'
                   for name in sorted(ports.keys() | declared.keys())
                   if ports.get(name) != declared.get(name)]
    if differences:
        raise ValueError('boundary ports differ from native hierarchical pins: ' + '; '.join(differences))
    parts = _read(root / record['parts_manifest'])
    if not isinstance(parts, dict) or parts.get('schema') not in ('agent-ee.parts/1', DRAFT_PARTS):
        raise ValueError('expected captured agent-ee.parts/1 subset or draft parts listing')
    if parts['schema'] != DRAFT_PARTS:
        validate_manifest(parts)
    _reference_set(root, record, parts)


def sheet_symbols(data, uuid_path):
    """Map references of one native sheet occurrence to their symbol identity.

    Units of one multi-unit part are separate placed symbols sharing a reference;
    they must agree on lib_id and on any nonempty footprint. Virtual ``#`` symbols
    are skipped, as KiCad omits them from netlists.
    """
    target = [s for s in uuid_path.split('/') if s]
    result = {}
    for symbol in children(parse_sexpr(data), 'symbol'):
        lib_id, = field(symbol, 'lib_id', 1)
        uuid, = field(symbol, 'uuid', 1)
        props = {p[1]: p[2] for p in children(symbol, 'property') if len(p) > 2}
        refs = [field(path, 'reference', 1)[0]
                for instances in children(symbol, 'instances')
                for project in children(instances, 'project')
                for path in children(project, 'path')
                if [s for s in path[1].split('/') if s][1:] == target]
        if not children(symbol, 'instances') and 'Reference' in props:
            # Pre-instance (20211123) sheets keep one Reference per symbol.
            refs = [props['Reference']]
        if len(refs) > 1:
            raise ValueError('ambiguous native symbol occurrence')
        # '#'-prefixed power and flag symbols are not netlist components.
        if not refs or refs[0].startswith('#'):
            continue
        entry = result.setdefault(refs[0], dict(lib_id=lib_id, footprint='', uuids=[]))
        footprint = props.get('Footprint', '')
        if entry['lib_id'] != lib_id or (footprint and entry['footprint'] not in ('', footprint)):
            raise ValueError('conflicting native units for reference: ' + refs[0])
        entry['footprint'] = entry['footprint'] or footprint
        entry['uuids'].append(uuid)
    return result


def _reference_set(root, record, parts):
    """Bind instance reference sets, libraries and generated documents to captured sheets."""
    if 'instances' not in record:
        for key in ('libraries', 'interface', 'connectivity'):
            if key in record:
                raise ValueError(key + ' requires explicit instance reference sets')
        if parts['schema'] == DRAFT_PARTS:
            raise ValueError('draft parts listing requires explicit instance reference sets')
        return
    members, used = {}, set()
    for instance in record['instances']:
        symbols = sheet_symbols((root / instance['sheet']).read_bytes(), instance['uuid_path'])
        for ref in instance['references']:
            if ref not in symbols:
                raise ValueError('reference absent from captured sheet occurrence: ' + ref)
            members[ref] = (instance['uuid_path'], symbols[ref])
            used.add(('symbol', symbols[ref]['lib_id']))
            if symbols[ref]['footprint']:
                used.add(('footprint', symbols[ref]['footprint']))
    if 'libraries' in record and {(l['kind'], l['lib_id']) for l in record['libraries']} != used:
        raise ValueError('library inventory differs from the reference set symbols and footprints')
    if parts['schema'] == DRAFT_PARTS:
        _object(parts, ('schema', 'source', 'catalog', 'parts'))
        _list(parts['parts'])
        listed = {}
        for part in parts['parts']:
            _object(part, ('instance', 'reference', 'lib_id', 'value', 'footprint', 'lcsc', 'mpn',
                           'manufacturer', 'populated', 'catalog'))
            listed[part['reference']] = part
            if (part['reference'] not in members or part['instance'] != members[part['reference']][0] or
                    part['lib_id'] != members[part['reference']][1]['lib_id']):
                raise ValueError('draft part is outside the reference set: ' + str(part['reference']))
        if listed.keys() != members.keys() or len(listed) != len(parts['parts']):
            raise ValueError('draft parts listing must cover the reference set once')
    if 'connectivity' in record:
        projection = _read(root / record['connectivity'])
        if (not isinstance(projection, dict) or projection.get('schema') != 'agent-ee.block-connectivity/1' or
                not isinstance(projection.get('components'), dict) or projection['components'].keys() != members.keys()):
            raise ValueError('connectivity projection must cover the reference set')
    if 'interface' in record:
        from .hardware_interface import capability
        specs = [c for c in record.get('check_set', []) if c['kind'] == 'hardware_interface']
        if not specs or any('source' not in r for r in record['requirements']):
            raise ValueError('interface needs a mapped hardware_interface check and requirement sources')
        scope = {'baseline': {'requirements': record['requirements']}}
        for spec in specs:
            contract = capability(spec['params'], scope, record['files'], root, spec['id'],
                                  block_sources=True)['contract']
            for component in contract['components'].values():
                for location in component['locations']:
                    if members.get(location['reference'], ('',))[0] != location['sheet']:
                        raise ValueError('interface component is outside the reference set')


def _native_block_subject(run, manifest, ref, record, native_id, checks):
    """Bind a sealed native export to the block's captured hierarchy occurrences."""
    from xml.etree import ElementTree as ET
    from .checks import sheet_instances

    scope = manifest['scope']
    specs = {c['id']: c for c in scope['checks']}
    native = checks.get(native_id, {})
    if (not scope.get('schematic') or specs.get(native_id, {}).get('kind') != 'netlist' or
            native.get('execution') != 'ok'):
        return None
    sheet = ref['files'][record['sheet']]
    files, _, missing = sheet_instances(run / 'inputs', scope['schematic'])
    if missing or sheet not in files or sheet not in native.get('details', {}).get('schematic_files', []):
        return None

    # Reconstruct native instance IDs from actual captured hierarchy, rather than
    # accepting author-written evaluated-files or ambiguous source basenames.
    occurrences = set()
    def walk(path, instance):
        if path == sheet:
            occurrences.add(instance)
        ast = parse_sexpr((run / 'inputs' / path).read_bytes())
        for child in children(ast, 'sheet'):
            props = {p[1]: p[2] for p in children(child, 'property')}
            child_path = (PurePosixPath(path).parent / props['Sheetfile']).as_posix()
            child_path = posixpath.normpath(child_path)
            uuid, = field(child, 'uuid', 1)
            walk(child_path, instance + uuid + '/')
    walk(scope['schematic'], '/')
    symbols = {field(symbol, 'uuid', 1)[0]
               for symbol in children(parse_sexpr((run / 'inputs' / sheet).read_bytes()), 'symbol')}
    xml = ET.parse(run / 'work' / native_id / 'design.xml')
    if xml.findtext('./design/source') != '/candidate/' + scope['schematic']:
        return None
    exported = {node.get('tstamps') for node in xml.findall('./design/sheet')}
    if not occurrences or not occurrences <= exported:
        return None
    return occurrences, symbols


def _native_requirement_coverage(run, manifest, ref, record, req, checks):
    """Recognize qualified native interface witnesses for this block instance."""
    from .hardware_interface import load_contract

    specs = {c['id']: c for c in manifest['scope']['checks']}
    if 'layout' in record or any(specs[c]['kind'] != 'hardware_interface' for c in req['checks']):
        return False
    for cid in req['checks']:
        spec = specs[cid]
        params = spec['params']
        native_id = params['netlist_check']
        subject = _native_block_subject(run, manifest, ref, record, native_id, checks)
        if subject is None:
            return False
        occurrences, symbols = subject
        contract = load_contract(run / 'inputs', params['contract'])
        expected = {i['id'] for i in contract['invariants'] if i['requirement'] == req['id']}
        witnesses = [i for i in checks[cid]['details']['invariants']
                     if i['expected']['requirement'] == req['id']]
        if not expected or {i['id'] for i in witnesses} != expected:
            return False
        for witness in witnesses:
            components = witness['actual'].get('components', {})
            if (witness['status'] != 'satisfied' or not components or
                    any(not n or n.get('sheet') not in occurrences or n.get('uuid') not in symbols
                        for n in components.values())):
                return False
    return True


def _parts_coverage(run, manifest, ref, record, checks):
    """Use only sealed membership checks over the exact manifest and native block."""
    covered = []
    for spec in manifest['scope']['checks']:
        if spec['kind'] != 'parts_subset' or spec['params']['manifest'] != ref['files'][record['parts_manifest']]:
            continue
        check = checks.get(spec['id'], {})
        if check.get('execution') != 'ok' or check.get('outcome') != 'pass':
            continue
        details = check.get('details', {})
        report = details.get('report', {})
        if (PREDICATE not in details.get('evaluated_predicates', []) or
                report.get('manifest_sha256') != record['files'][record['parts_manifest']] or
                report.get('execution') != 'ok' or report.get('outcome') != 'pass' or
                report.get('coverage') != 'complete'):
            continue
        native_id = spec['params']['netlist_check']
        subject = _native_block_subject(run, manifest, ref, record, native_id, checks)
        if subject is None:
            continue
        occurrences, symbols = subject
        if report.get('native_xml_sha256') != digest(run / 'work' / native_id / 'design.xml'):
            continue
        if any(m.get('status') == 'pass' and m.get('sheet') in occurrences and m.get('uuid') in symbols
               for m in report.get('matches', [])):
            covered.append(spec['id'])
    return covered


def validate_block(directory, run_locations=None, *, repository_locations=None):
    """Refuse inconsistent records; return unverified for missing passing coverage."""
    root = Path(directory)
    if root.is_symlink():
        raise ValueError('block root is a symlink')
    record = _read(root / 'block.json')
    _structure(record)
    _payload(root, record)
    citations = []
    for req in record['requirements'] + record.get('pending_requirements', []):
        source = req.get('source', {})
        if source.get('kind') == 'pinned-external':
            location = (repository_locations or {}).get(source['repository'])
            citations.append(dict(requirement=req['id'], source=source,
                                  **verify_external_source(source, location)))
    covered, runs, parts_evidence = set(), [], []
    locations = run_locations or {}
    for ref in record['runs']:
        rid = ref['run_id']
        if rid not in locations:
            runs.append(dict(run_id=rid, status='unavailable'))
            continue
        # Public verification retains the coordinator seal, policy and native
        # execution authority; this module does not replay or replace those rules.
        from .cli import verify
        from .baseline import load_baseline, verify_evidence
        from .locking import reader_lock
        run = Path(locations[rid]).resolve()
        with reader_lock(run):
            result = verify(run, current_source=False)
            baseline = load_baseline(run)
            manifest = _read(run / 'candidate.json')
            if (result['run_id'] != rid or result['candidate_id'] != ref['candidate_id'] or
                    baseline is None or baseline['baseline_id'] != ref['baseline_id']):
                raise ValueError('referenced run/candidate/baseline identity differs')
            for path, captured in ref['files'].items():
                if (manifest['files'].get(captured) != record['files'][path] or
                        digest(run / 'inputs' / captured) != record['files'][path]):
                    raise ValueError('referenced run does not cover exact block bytes: ' + path)
            passing = result['state'] == 'complete' and result['outcome'] == 'criteria_satisfied'
            if passing:
                verify_evidence(run, result['checks'], required=True)
                requirements = {r['id']: r for r in baseline['requirements']}
                checks = {c['id']: c for c in result['checks']}
                parts_evidence.extend(dict(run_id=rid, check_id=cid,
                    manifest_sha256=record['files'][record['parts_manifest']])
                    for cid in _parts_coverage(run, manifest, ref, record, checks))
                for req in record['requirements']:
                    bound = requirements.get(req['id'])
                    if bound is None or bound.get('intent', 'acceptance') != 'acceptance':
                        continue
                    if bound['statement'] != req['statement'] or not set(req['checks']) <= set(bound['checks']):
                        raise ValueError('block requirement differs from baseline')
                    if 'source' in req:
                        source = req['source']
                        expected = (dict(path=ref['files']['block-contract.json'],
                                         locator='requirements/' + req['id'] + '/source')
                                    if source.get('kind') == 'pinned-external' else
                                    dict(path=ref['files'][source['path']], locator=source['locator']))
                        if bound.get('source') != expected:
                            raise ValueError('block requirement source differs from baseline')
                    if all(checks.get(c, {}).get('execution') == 'ok' and
                           checks[c]['outcome'] == 'pass' for c in req['checks']) and _native_requirement_coverage(
                               run, manifest, ref, record, req, checks):
                        covered.add(req['id'])
            runs.append(dict(run_id=rid, status='passing' if passing else 'unverified'))
    # Detect mutation at the read boundary; trusted-local consistency, not a
    # signature or protection from a hostile host rewriting all expected hashes.
    if _read(root / 'block.json') != record:
        raise ValueError('block metadata changed during validation')
    _payload(root, record)
    declared = all(p['current_a'] is not None and p['voltage_domain'] is not None
                   for p in record['boundary_ports'])
    pending = [r['id'] for r in record.get('pending_requirements', [])]
    unresolved = [l['kind'] + ':' + l['lib_id'] for l in record.get('libraries', [])
                  if l['origin'] == 'unresolved' or l['sha256'] is None]
    draft_parts = _read(root / record['parts_manifest'])['schema'] == DRAFT_PARTS
    complete = (declared and not pending and not unresolved and not draft_parts and
                all(c['status'] == 'verified' for c in citations))
    return dict(schema='agent-ee.block-report/1', block_id=record['id'],
                status='verified' if complete and parts_evidence and runs and all(r['status'] == 'passing' for r in runs) and covered == {r['id'] for r in record['requirements']} else 'unverified',
                pending_requirements=pending, unresolved_libraries=unresolved, draft_parts=draft_parts,
                external_sources=citations,
                covered_requirements=sorted(covered), runs=runs,
                parts_membership=dict(status='verified' if parts_evidence else 'unverified', evidence=parts_evidence),
                missing_requirements=sorted({r['id'] for r in record['requirements']} - covered),
                undeclared_ports=[p['name'] for p in record['boundary_ports']
                                  if p['current_a'] is None or p['voltage_domain'] is None],
                limitations=['Provenance only; adaptations require fresh board checks.',
                             'Declared parts membership is not owner approval, live availability or hardware acceptance.',
                             'Run references do not establish a containing campaign\'s current escalation state.',
                             'Only native hardware-interface requirement subjects are qualified; '
                             'other check families and optional layouts remain unverified.'])


def _git(repo, *args):
    # Read the named objects, without local replacements or promisor downloads.
    # Denying transports also covers Git versions without the lazy-fetch guard.
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='')
    proc = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=True, env=env)
    return proc.stdout


def verify_external_source(source, repository=None):
    """Verify citation identity locally; unavailable objects never earn credit.

    Repository identities may be absolute local paths or names resolved by the
    caller. No remote lookup, checkout file or lazy fetch supplies missing bytes.
    """
    _source(source, ())
    if source.get('kind') != 'pinned-external':
        raise ValueError('expected pinned-external source')
    repo = Path(repository if repository is not None else source['repository'])
    if (repository is None and not repo.is_absolute()) or not repo.is_dir():
        return dict(status='unverified', detail='Local repository unavailable')
    commit, path = source['commit'], source['path']
    try:
        kind = _git(repo, 'cat-file', '-t', commit).strip()
        if kind != b'commit':
            raise ValueError('external source identity is not a commit')
        entry = _git(repo, 'ls-tree', '-z', commit, '--', ':(literal)' + path).split(b'\x00')
        if len(entry) != 2 or not entry[0].startswith((b'100644 blob ', b'100755 blob ')):
            raise ValueError('external source is not one regular committed file: ' + path)
        blob = entry[0].split(b'\t', 1)[0].split()[2].decode('ascii')
        if blob != source['blob']:
            raise ValueError('external source blob differs at pinned commit: ' + path)
        raw = _git(repo, 'cat-file', 'blob', blob)
    except subprocess.CalledProcessError as exc:
        return dict(status='unverified', detail='Local Git object unavailable: ' +
                    exc.stderr.decode('utf-8', errors='replace').strip())
    if hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise ValueError('external source SHA-256 differs: ' + path)
    return dict(status='verified', detail='Pinned commit/path/blob and SHA-256 verified offline')


def capture_block(request, repository, commit, destination, *, parts=None, supplements=None,
                  repository_label=None):
    """Copy pinned local Git blobs only; never update source or fetch objects.

    ``supplements`` maps payload paths to caller-generated bytes (for example a
    derived interface contract); they are hashed and run-covered like the parts file.
    ``repository_label`` replaces the resolved local path in ``source.repository``
    so a committed capture does not depend on where the source was cloned.
    """
    record = copy.deepcopy(request)
    if not isinstance(record, dict) or not isinstance(record.get('source'), dict):
        raise ValueError('capture request needs source object')
    _object(record['source'], ('repository', 'commit', 'paths', 'license'), ('upstream_origin', 'upstream_license'))
    if ('upstream_origin' in record['source']) != ('upstream_license' in record['source']):
        raise ValueError('source upstream_origin and upstream_license must be paired')
    for key in (('upstream_origin', 'upstream_license') if 'upstream_origin' in record['source'] else ()):
        _text(record['source'][key])
    if not isinstance(record['source']['paths'], dict) or not record['source']['paths']:
        raise ValueError('capture request needs source paths')
    if 'parts_manifest' not in record:
        raise ValueError('capture request needs parts_manifest')
    _path(record['parts_manifest'])
    _hash(commit, 40)
    pinned = _git(repository, 'rev-parse', '--verify', commit + '^{commit}').decode().strip()
    if pinned != commit:
        raise ValueError('source commit must be full exact commit')
    record['source']['repository'] = str(Path(repository).resolve()) if repository_label is None else repository_label
    record['source']['commit'] = pinned
    payload = {}
    for local, source in record['source']['paths'].items():
        _path(local)
        _path(source)
        # Reject trees, submodules and links, even though git show can read a
        # link target string. Source statements bind ordinary committed blobs.
        entry = _git(repository, 'ls-tree', '-z', commit, '--', ':(literal)' + source).split(b'\x00')
        if len(entry) != 2 or not entry[0].startswith((b'100644 blob ', b'100755 blob ')):
            raise ValueError('source is not one regular committed file: ' + source)
        payload[local] = _git(repository, 'show', commit + ':' + source)
    if parts is not None:
        path = record['parts_manifest']
        _path(path)
        if path in payload or Path(parts).is_symlink():
            raise ValueError('parts supplement overlaps source or is a link')
        payload[path] = Path(parts).read_bytes()
    for path, data in (supplements or {}).items():
        _path(path)
        if path in payload or not isinstance(data, bytes):
            raise ValueError('generated supplement overlaps captured payload')
        payload[path] = data
    if 'block-contract.json' in payload:
        raise ValueError('reserved generated contract path')
    payload['block-contract.json'] = canonical(block_contract(record))
    record['files'] = {p: hashlib.sha256(data).hexdigest() for p, data in payload.items()}
    _structure(record)
    hierarchy_ports(payload[record['sheet']])
    target = Path(destination)
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError('capture destination has a symlink ancestor')
    target.mkdir(mode=0o700, parents=False, exist_ok=False)
    for path, data in payload.items():
        out = target / path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    write_json(target / 'block.json', record)
    return validate_block(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    capture = commands.add_parser('capture')
    capture.add_argument('request')
    capture.add_argument('--repo', required=True)
    capture.add_argument('--commit', required=True)
    capture.add_argument('--out', required=True)
    capture.add_argument('--parts', help='explicit local parts/1 subset supplement')
    validate = commands.add_parser('validate')
    validate.add_argument('directory')
    validate.add_argument('--run', action='append', default=[], metavar='ID=PATH')
    validate.add_argument('--repository', action='append', default=[], metavar='ID=PATH',
                          help='local Git object store for a pinned-external repository identity')
    args = parser.parse_args(argv)
    try:
        if args.command == 'capture':
            report = capture_block(_read(args.request), args.repo, args.commit, args.out, parts=args.parts)
        else:
            locations = {}
            for value in args.run:
                key, sep, path = value.partition('=')
                if not sep or not key or not path or key in locations:
                    raise ValueError('run location must be unique ID=PATH')
                locations[key] = path
            repositories = {}
            for value in args.repository:
                key, sep, path = value.partition('=')
                if not sep or not key or not path or key in repositories:
                    raise ValueError('repository location must be unique ID=PATH')
                repositories[key] = path
            report = validate_block(args.directory, locations, repository_locations=repositories)
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(json.dumps(dict(schema='agent-ee.block-report/1', status='refused', error=str(exc))))
        return 2
    except Exception as exc:
        # At the CLI boundary, crashes need a distinct exit from valid unknown
        # coverage. Keep the traceback for diagnosis and never emit acceptance.
        traceback.print_exc()
        print(json.dumps(dict(schema='agent-ee.block-report/1', status='error',
                              error=type(exc).__name__ + ': ' + str(exc))))
        return 3
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
