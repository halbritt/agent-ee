"""Offline per-LCSC identity records from captured JLCPCB and datasheet inputs."""
import argparse
import hashlib
from pathlib import Path

from .catalog import exact_fields, nonempty, strict_json
from .datasheet_facts import load_acquisition
from .jlc_stock_capture import LCSC_RE, validate_capture
from .storage import canonical, digest

SCHEMA = 'agent-ee.part-record/1'
FIELDS = ('lcsc', 'mpn', 'manufacturer', 'package', 'category', 'library_tier', 'stock',
          'fetched_at', 'response_sha256', 'datasheet_url')
LIMIT = ('Captured exact identity only; no parametric ranking, suitability, datasheet '
         'applicability, live stock or owner approval is established.')


def _sources(root, catalog, datasheets, datasheet_id, lcsc):
    # Local import avoids a module cycle: parts_manifest owns captured-path rules.
    from .parts_manifest import _blob, _read_blob, read_captured
    _blob(catalog)
    _blob(datasheets)
    if not isinstance(lcsc, str) or not LCSC_RE.fullmatch(lcsc):
        raise ValueError('exact LCSC required')
    nonempty(datasheet_id)
    stock = strict_json(_read_blob(root, catalog))
    parent = Path(catalog['path']).parent
    response_blobs = {}

    def read_response(rel):
        path = (parent / rel).as_posix()
        raw = read_captured(root, path)
        response_blobs[path] = hashlib.sha256(raw).hexdigest()
        return raw

    validate_capture(stock, read_response)
    if lcsc not in stock['parts']:
        raise ValueError('named part has no successful captured JLCPCB row')
    row = stock['parts'][lcsc]
    request = next(r for r in stock['requests'] if r['lcsc'] == lcsc)
    acquisition = strict_json(_read_blob(root, datasheets))
    # The producer's complete binding digest is rederived, including other IDs.
    records = acquisition.get('parts') if isinstance(acquisition, dict) else None
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError('datasheet acquisition requires records')
    bindings = sorted([dict(id=r['id'], datasheet_url=r['url']) for r in records], key=lambda r: r['id'])
    acquired, pdf_paths = load_acquisition(root, datasheets, bindings)
    sheet = acquired.get(datasheet_id)
    if sheet is None or sheet['missing']:
        raise ValueError('named part requires a captured datasheet')
    observation = dict(lcsc=lcsc, mpn=row['mpn'], manufacturer=row['mfr'], package=row['package'],
                       category=row['category'], library_tier=row['library_tier'], stock=row['stock'],
                       fetched_at=request['fetched_at'], response_sha256=request['response']['sha256'],
                       datasheet_url=sheet['url'])
    for key in FIELDS:
        if key != 'stock':
            nonempty(observation[key])
    dependencies = {catalog['path']: catalog['sha256'], datasheets['path']: datasheets['sha256'],
                    **response_blobs}
    # Use the manifest reader too, so all paths obey one canonical capture policy.
    for path in pdf_paths:
        dependencies[path] = hashlib.sha256(read_captured(root, path)).hexdigest()
    artifact = dict(path=(Path(datasheets['path']).parent / sheet['path']).as_posix(), sha256=sheet['sha256'])
    return observation, artifact, dict(sorted(dependencies.items()))


def prepare_record(root, lcsc, catalog, datasheets, datasheet_id, *, source_selection=None):
    """Derive one record from existing captures; performs no acquisition or writes."""
    from .datasheet_selection import acquisition_source, check_binding
    if source_selection is not None:
        datasheets, datasheet_id = acquisition_source(root, source_selection, context=lcsc)
    observation, artifact, _ = _sources(root, catalog, datasheets, datasheet_id, lcsc)
    record = dict(schema=SCHEMA, **observation, catalog=dict(catalog),
                  datasheets=dict(datasheets), datasheet_id=datasheet_id, artifact=artifact)
    if source_selection is not None:
        check_binding(root, source_selection, artifact,
                      {**datasheets, 'locator': 'parts[id=' + datasheet_id + ']'}, context=lcsc)
        record['source_selection'] = dict(source_selection)
    return record


def qualify_record(document, root):
    """Replay identity and return the complete immutable capture dependency map."""
    fields = ('schema', *FIELDS, 'catalog', 'datasheets', 'datasheet_id', 'artifact')
    exact_fields(document, (*fields, 'source_selection') if 'source_selection' in document else fields)
    if type(document['stock']) is not int or document['stock'] < 0:
        raise ValueError('part-record stock must be a historical nonnegative integer')
    if document['schema'] != SCHEMA:
        raise ValueError('unsupported part-record schema')
    observation, artifact, dependencies = _sources(root, document['catalog'], document['datasheets'],
                                                  document['datasheet_id'], document['lcsc'])
    if any(document[k] != observation[k] for k in FIELDS) or document['artifact'] != artifact:
        raise ValueError('part record differs from captured identity')
    if 'source_selection' in document:
        from .datasheet_selection import check_binding
        ref = document['source_selection']
        check_binding(root, ref, artifact,
                      {**document['datasheets'], 'locator': 'parts[id=' + document['datasheet_id'] + ']'},
                      context=document['lcsc'])
        dependencies[ref['path']] = ref['sha256']
    return dict(sorted(dependencies.items()))


def _external_sources(root, identity_source, datasheets, datasheet_id, deviation_ref):
    from .parts_manifest import _blob, _read_blob, read_captured
    from .part_decisions import validate_deviation
    _blob(identity_source, True)
    if identity_source['locator'] != '/':
        raise ValueError('external identity locator must name the whole typed declaration: /')
    declaration = strict_json(_read_blob(root, identity_source))
    exact_fields(declaration, ('schema', 'mpn', 'manufacturer', 'package', 'evidence_refs'))
    if declaration['schema'] != 'agent-ee.external-part-identity/1':
        raise ValueError('expected captured external-part-identity/1 declaration')
    for field in ('mpn', 'manufacturer', 'package'):
        nonempty(declaration[field])
    dependencies = {identity_source['path']: identity_source['sha256']}
    refs = declaration['evidence_refs']
    if not isinstance(refs, list) or not refs:
        raise ValueError('external identity needs captured source evidence')
    for ref in refs:
        _blob(ref, True)
        if not _read_blob(root, ref):
            raise ValueError('empty external identity source')
        dependencies[ref['path']] = ref['sha256']
    exact_fields(deviation_ref, ('path', 'sha256', 'index'))
    deviation_blob = {k: deviation_ref[k] for k in ('path', 'sha256')}
    _blob(deviation_blob)
    deviations = strict_json(_read_blob(root, deviation_blob))
    exact_fields(deviations, ('schema', 'deviations'))
    index = deviation_ref['index']
    if (deviations['schema'] != 'agent-ee.part-deviations/1' or
            not isinstance(deviations['deviations'], list) or type(index) is not int or
            not 0 <= index < len(deviations['deviations'])):
        raise ValueError('external part needs a pinned assembly deviation index')
    deviation = deviations['deviations'][index]
    if (not validate_deviation(deviation) or
            deviation['original'] != {'mpn': declaration['mpn'], 'lcsc': None}):
        raise ValueError('external identity differs from hand-fit deviation')
    dependencies[deviation_ref['path']] = deviation_ref['sha256']
    for ref in deviation['evidence_refs']:
        exact_fields(ref, ('path', 'locator'))
        nonempty(ref['locator'])
        raw = read_captured(root, ref['path'])
        if not raw:
            raise ValueError('empty hand-fit evidence')
        dependencies[ref['path']] = hashlib.sha256(raw).hexdigest()
    _blob(datasheets)
    acquisition = strict_json(_read_blob(root, datasheets))
    bindings = sorted([dict(id=r['id'], datasheet_url=r['url']) for r in acquisition['parts']], key=lambda r: r['id'])
    acquired, paths = load_acquisition(root, datasheets, bindings)
    nonempty(datasheet_id)
    sheet = acquired.get(datasheet_id)
    if sheet is None or sheet['missing']:
        raise ValueError('external part requires a captured datasheet')
    for path in paths:
        dependencies[path] = hashlib.sha256(read_captured(root, path)).hexdigest()
    observed = {k: declaration[k] for k in ('mpn', 'manufacturer', 'package')}
    observed.update(lcsc='NO_LCSC', stock=None, datasheet_url=sheet['url'])
    artifact = dict(path=(Path(datasheets['path']).parent / sheet['path']).as_posix(), sha256=sheet['sha256'])
    return observed, artifact, dict(sorted(dependencies.items()))


def prepare_external_record(root, identity_source, datasheets, datasheet_id, deviation_ref):
    """Replay an explicit external identity declaration; never invent a supplier row."""
    observed, artifact, _ = _external_sources(root, identity_source, datasheets, datasheet_id, deviation_ref)
    return dict(schema='agent-ee.external-part-record/1', **observed, identity_source=identity_source,
                datasheets=datasheets, datasheet_id=datasheet_id, artifact=artifact, deviation_ref=deviation_ref)


def qualify_external_record(document, root):
    exact_fields(document, ('schema', 'mpn', 'manufacturer', 'package', 'lcsc', 'stock', 'datasheet_url',
                            'identity_source', 'datasheets', 'datasheet_id', 'artifact', 'deviation_ref'))
    if document['schema'] != 'agent-ee.external-part-record/1':
        raise ValueError('unsupported external part record')
    observed, artifact, dependencies = _external_sources(root, document['identity_source'], document['datasheets'],
                                                        document['datasheet_id'], document['deviation_ref'])
    if any(document[k] != v for k, v in observed.items()) or document['artifact'] != artifact:
        raise ValueError('external part record differs from captured identity/deviation')
    return dependencies

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--lcsc', required=True)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--datasheets', required=True)
    parser.add_argument('--datasheet-id', required=True)
    parser.add_argument('--source-selection', help='captured source-selection JSON relative to root')
    parser.add_argument('--source-part-key', help='exact key in source selection (defaults to LCSC)')
    parser.add_argument('--out', required=True, help='new canonical path relative to root')
    args = parser.parse_args(argv)
    from .parts_manifest import _path, read_captured
    try:
        def blob(path):
            return dict(path=path, sha256=hashlib.sha256(read_captured(args.root, path)).hexdigest())
        ref = ({**blob(args.source_selection), 'part_key': args.source_part_key or args.lcsc}
               if args.source_selection else None)
        if args.source_part_key and ref is None:
            raise ValueError('--source-part-key requires --source-selection')
        record = prepare_record(args.root, args.lcsc, blob(args.catalog), blob(args.datasheets),
                                args.datasheet_id, source_selection=ref)
        output = Path(args.root) / _path(args.out)
        # Resolve the parent by the same no-link policy as captured inputs.
        if output.parent.resolve(strict=True) != output.parent.absolute():
            raise ValueError('output parent must be real and canonical')
        with output.open('xb') as stream:
            stream.write(canonical(record) + b'\n')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + '\n')
    print(digest(output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
