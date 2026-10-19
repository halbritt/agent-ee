"""Strict captured parts proposals and their immutable dependency closure.

A declaration source is an allowlist input, not authenticated owner approval.
Selection and hashes cannot promote an author's declaration to that authority.
"""
import copy
import hashlib
import re
from pathlib import Path

from .candidate import safe_relative
from .catalog import exact_fields, nonempty, strict_json
from .footprint_library import _members, check_assignments, validate_declaration
from .part_selection import selection_check
from .storage import identity

SCHEMA = 'agent-ee.parts/1'
PREDICATE = 'parts:declared-membership-multiplicity/1'
IDENTITY_FIELDS = ('lib_id', 'footprint', 'lcsc', 'mpn', 'manufacturer')
LIMIT = ('Declared native membership/multiplicity only. Captured declarations and catalog claims '
         'are not authenticated owner approval, live stock, assembly readiness, datasheet '
         'applicability, component suitability, procurement or hardware acceptance.')


def _path(value):
    nonempty(value)
    if str(safe_relative(value)) != value or '\\' in value:
        raise ValueError('canonical captured path required')
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('SHA-256 required')
    return value


def _blob(value, locator=False):
    exact_fields(value, ('path', 'sha256', 'locator') if locator else ('path', 'sha256'))
    _path(value['path'])
    _sha(value['sha256'])
    if locator:
        nonempty(value['locator'])


def _validate_generic_sources(document):
    catalog = document['catalog']
    exact_fields(catalog, ('snapshot', 'manifest_sha256', 'database_sha256', 'snapshot_date'))
    _path(catalog['snapshot'])
    _sha(catalog['manifest_sha256'])
    _sha(catalog['database_sha256'])
    nonempty(catalog['snapshot_date'])
    exact_fields(document['selection'], ('requirements', 'result'))
    for blob in document['selection'].values():
        _blob(blob)


def named_selection(part):
    """A named selection is explicit; unknown kinds are rejected by validation."""
    return isinstance(part, dict) and 'selection' in part


def _named_blob(part):
    selection = part['selection']
    return dict(path=selection['part_record'], sha256=selection['part_record_sha256'])


def validate_manifest(document):
    """Validate the complete versioned proposal; no I/O or authority inference."""
    exact_fields(document, ('schema', 'declaration_source', 'catalog', 'selection', 'cad',
                            'property_fields', 'population', 'parts'))
    if document['schema'] != SCHEMA:
        raise ValueError('unsupported parts schema; experimental rung records require migration')
    _blob(document['declaration_source'], True)
    if document['population'] != 'native-dnp-optional-only/1':
        raise ValueError('unsupported captured population policy')
    fields = document['property_fields']
    exact_fields(fields, ('lcsc', 'mpn', 'manufacturer'))
    if len(set(nonempty(v) for v in fields.values())) != 3:
        raise ValueError('distinct exact native property fields required')
    if document['catalog'] is not None:
        _validate_generic_sources(document)
    elif document['selection'] is not None:
        raise ValueError('catalog and selection must both be null for named-only parts')
    exact_fields(document['cad'], ('manifest', 'assignments', 'members_sha256'))
    for name in ('manifest', 'assignments'):
        _blob(document['cad'][name])
    _sha(document['cad']['members_sha256'])
    parts = document['parts']
    if not isinstance(parts, list) or not parts or len(parts) > 4096:
        raise ValueError('nonempty bounded parts required')
    ids, selectors, selection_ids = set(), set(), set()
    for part in parts:
        fields = ('id', 'role', 'required', 'instances', 'selection_id', *IDENTITY_FIELDS,
                  'stock', 'datasheet')
        if named_selection(part):
            fields += ('selection', 'package')
        exact_fields(part, (*fields, 'pin_contract') if isinstance(part, dict) and 'pin_contract' in part else fields)
        if named_selection(part):
            selection = part['selection']
            exact_fields(selection, ('kind', 'lcsc', 'part_record', 'part_record_sha256', 'decision_ref'))
            if selection['kind'] != 'named' or selection['lcsc'] != part['lcsc']:
                raise ValueError('named selection kind/LCSC mismatch')
            _blob(_named_blob(part))
            _blob(selection['decision_ref'], True)
            nonempty(part['package'])
            if not isinstance(part['datasheet'], dict) or part['datasheet'].get('status') != 'captured':
                raise ValueError('named part requires a captured datasheet')
        elif document['catalog'] is None:
            raise ValueError('generic parts require canonical catalog selection')
        if 'pin_contract' in part:
            _blob(part['pin_contract'])
        for field in ('id', 'role', 'selection_id', *IDENTITY_FIELDS):
            nonempty(part[field])
        if part['id'] in ids or part['selection_id'] in selection_ids:
            raise ValueError('duplicate logical part or selection allocation')
        ids.add(part['id'])
        selection_ids.add(part['selection_id'])
        if type(part['required']) is not bool:
            raise ValueError('required must be boolean')
        for field in ('lib_id', 'footprint'):
            if part[field].count(':') != 1 or not all(part[field].split(':')):
                raise ValueError('exact library:member identity required')
        if not re.fullmatch('C[1-9][0-9]*', part['lcsc']):
            raise ValueError('exact LCSC required')
        if part['stock'] is not None and (type(part['stock']) is not int or part['stock'] < 0):
            raise ValueError('stock must be historical integer or null')
        if not isinstance(part['instances'], list) or not part['instances']:
            raise ValueError('explicit nonempty instance capacity required')
        for selector in part['instances']:
            exact_fields(selector, ('sheet', 'reference'))
            sheet, ref = nonempty(selector['sheet']), nonempty(selector['reference'])
            if not sheet.startswith('/') or not sheet.endswith('/') or (sheet, ref) in selectors:
                raise ValueError('unique sheet/reference selector required')
            selectors.add((sheet, ref))
        sheet = part['datasheet']
        if not isinstance(sheet, dict) or type(sheet.get('required')) is not bool:
            raise ValueError('explicit datasheet obligation required')
        if sheet.get('status') == 'captured':
            exact_fields(sheet, ('status', 'required', 'artifact', 'provenance'))
            _blob(sheet['artifact'])
            _blob(sheet['provenance'], True)
        elif sheet.get('status') == 'unavailable':
            exact_fields(sheet, ('status', 'required', 'reason'))
            nonempty(sheet['reason'])
        else:
            raise ValueError('explicit datasheet identity or unavailable reason required')
    return document


def read_captured(root, path):
    """Refuse links at every component and all nonregular capture members."""
    import stat
    path = _path(path)
    current = Path(root)
    for component in Path(path).parts:
        current = current / component
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError('captured dependency is a symlink: ' + path)
    if not stat.S_ISREG(mode):
        raise ValueError('captured dependency is not regular: ' + path)
    return current.read_bytes()


def load_manifest(root, path):
    return validate_manifest(strict_json(read_captured(root, path)))


def _read_blob(root, blob):
    payload = read_captured(root, blob['path'])
    if hashlib.sha256(payload).hexdigest() != blob['sha256']:
        raise ValueError('captured dependency hash mismatch: ' + blob['path'])
    return payload


def dependency_closure(document, root):
    """Validate pins and return every immutable path/hash, excluding candidate CAD."""
    validate_manifest(document)
    blobs = [document['declaration_source'], document['cad']['manifest'], document['cad']['assignments']]
    if document['selection'] is not None:
        blobs += list(document['selection'].values())
    catalog = document['catalog']
    if catalog is not None:
        blobs += [dict(path=catalog['snapshot'] + '/manifest.json', sha256=catalog['manifest_sha256']),
                  dict(path=catalog['snapshot'] + '/catalog.sqlite3', sha256=catalog['database_sha256'])]
    members = _members(strict_json(_read_blob(root, document['cad']['manifest'])))
    declaration = strict_json(_read_blob(root, document['cad']['assignments']))
    validate_declaration(declaration)
    cad_blobs = [member for _, member in members] + [
        {k: library[k] for k in ('path', 'sha256')} for library in declaration['libraries']]
    if len({m['path'] for m in cad_blobs}) != len(cad_blobs):
        raise ValueError('duplicate CAD member path')
    if identity(sorted(cad_blobs, key=lambda m: m['path'])) != document['cad']['members_sha256']:
        raise ValueError('CAD member closure identity mismatch')
    blobs += cad_blobs
    for part in document['parts']:
        if named_selection(part):
            from .part_record import qualify_record
            record_blob = _named_blob(part)
            record = strict_json(_read_blob(root, record_blob))
            dependencies = qualify_record(record, root)
            blobs += [record_blob, part['selection']['decision_ref']]
            blobs += [dict(path=p, sha256=h) for p, h in dependencies.items()]
        if 'pin_contract' in part:
            blobs.append(part['pin_contract'])
        if part['datasheet']['status'] == 'captured':
            blobs += [part['datasheet']['artifact'], part['datasheet']['provenance']]
    closure = {}
    for blob in blobs:
        _blob(blob, 'locator' in blob)
        _read_blob(root, blob)
        if blob['path'] in closure and closure[blob['path']] != blob['sha256']:
            raise ValueError('contradictory dependency pin')
        closure[blob['path']] = blob['sha256']
    return dict(sorted(closure.items()))


def qualify_manifest(document, root):
    """Rederive selection and captured CAD identities; never authenticate approval."""
    closure = dependency_closure(document, root)
    generic = [p for p in document['parts'] if not named_selection(p)]
    selection, choices, quantities = None, {}, {}
    if document['catalog'] is not None:
        requirements = strict_json(_read_blob(root, document['selection']['requirements']))
        expected = strict_json(_read_blob(root, document['selection']['result']))
        catalog = document['catalog']
        selection = selection_check(Path(root) / catalog['snapshot'], catalog['manifest_sha256'], requirements)
        if selection != expected or selection['execution'] != 'ok' or selection['outcome'] != 'pass':
            raise ValueError('canonical captured selection unavailable or differs from rederivation')
        if (selection['snapshot_sha256'] != catalog['database_sha256'] or
                selection['snapshot_date'] != catalog['snapshot_date']):
            raise ValueError('catalog snapshot identity mismatch')
        choices = {p['id']: p['chosen'] for p in selection['parts']}
        quantities = {p['id']: p['quantity_per_board'] for p in requirements['parts']}
    if set(choices) != {p['selection_id'] for p in generic}:
        raise ValueError('selection allocation coverage differs from declared generic parts')
    declaration = strict_json(_read_blob(root, document['cad']['assignments']))
    assignments = {c['reference']: c for c in declaration['components']}
    if set(assignments) != {p['id'] for p in document['parts']}:
        raise ValueError('CAD assignments must name each logical part exactly once')
    missing = []
    for part in document['parts']:
        if named_selection(part):
            record = strict_json(_read_blob(root, _named_blob(part)))
            if any(part[k] != record[k] for k in ('lcsc', 'mpn', 'manufacturer', 'package', 'stock')):
                raise ValueError('named identity/stock differs from captured part record: ' + part['id'])
            sheet = part['datasheet']
            if (sheet['artifact'] != record['artifact'] or
                    {k: sheet['provenance'][k] for k in ('path', 'sha256')} != record['datasheets'] or
                    sheet['provenance']['locator'] != 'parts[id=' + record['datasheet_id'] + ']'):
                raise ValueError('named datasheet differs from captured part record')
        else:
            row = choices[part['selection_id']]
            if any(part[field] != row[key] for field, key in
                   (('lcsc', 'lcsc'), ('mpn', 'mpn'), ('manufacturer', 'mfr'), ('stock', 'stock'))):
                raise ValueError('declared identity/stock differs from canonical catalog row: ' + part['id'])
            if len(part['instances']) != quantities[part['selection_id']]:
                raise ValueError('instance capacity differs from per-board selection quantity')
        if any(part[k] != assignments[part['id']][k] for k in ('lib_id', 'footprint')):
            raise ValueError('CAD identity differs from logical part: ' + part['id'])
        if part['datasheet']['status'] == 'unavailable' and part['datasheet']['required']:
            missing.append(dict(part_id=part['id'], reason=part['datasheet']['reason']))
    cad_manifest = strict_json(_read_blob(root, document['cad']['manifest']))
    captures = {p: read_captured(root, p) for p in closure}
    cad = check_assignments(cad_manifest, captures, declaration, native_membership=True)
    if cad['execution'] != 'ok' or cad['outcome'] != 'pass' or cad['coverage'] != 'complete':
        raise ValueError('captured CAD assignments unavailable or inconsistent')
    return dict(dependencies=closure, selection=selection, cad=cad, missing_provenance=missing)


def prepare_manifest(proposal, root):
    """Populate historical row claims from canonical selection; return a proposal.

    The caller supplies the explicit declaration source, CAD and datasheet inputs.
    This function creates neither an approval receipt nor approval authority.
    """
    document = copy.deepcopy(proposal)
    generic = [p for p in document['parts'] if not named_selection(p)]
    if generic:
        catalog = document['catalog']
        requirements = strict_json(_read_blob(root, document['selection']['requirements']))
        selection = selection_check(Path(root) / catalog['snapshot'], catalog['manifest_sha256'], requirements)
        if selection.get('outcome') != 'pass':
            raise ValueError('canonical selection unavailable')
        choices = {p['id']: p['chosen'] for p in selection['parts']}
        for part in generic:
            row = choices[part['selection_id']]
            part.update(lcsc=row['lcsc'], mpn=row['mpn'], manufacturer=row['mfr'], stock=row['stock'])
    qualify_manifest(document, root)
    return document
