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


def prepare_record(root, lcsc, catalog, datasheets, datasheet_id):
    """Derive one record from existing captures; performs no acquisition or writes."""
    observation, artifact, _ = _sources(root, catalog, datasheets, datasheet_id, lcsc)
    return dict(schema=SCHEMA, **observation, catalog=dict(catalog),
                datasheets=dict(datasheets), datasheet_id=datasheet_id, artifact=artifact)


def qualify_record(document, root):
    """Replay identity and return the complete immutable capture dependency map."""
    exact_fields(document, ('schema', *FIELDS, 'catalog', 'datasheets', 'datasheet_id', 'artifact'))
    if type(document['stock']) is not int or document['stock'] < 0:
        raise ValueError('part-record stock must be a historical nonnegative integer')
    if document['schema'] != SCHEMA:
        raise ValueError('unsupported part-record schema')
    observation, artifact, dependencies = _sources(root, document['catalog'], document['datasheets'],
                                                  document['datasheet_id'], document['lcsc'])
    if any(document[k] != observation[k] for k in FIELDS) or document['artifact'] != artifact:
        raise ValueError('part record differs from captured identity')
    return dependencies


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--lcsc', required=True)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--datasheets', required=True)
    parser.add_argument('--datasheet-id', required=True)
    parser.add_argument('--out', required=True, help='new canonical path relative to root')
    args = parser.parse_args(argv)
    from .parts_manifest import _path, read_captured
    try:
        def blob(path):
            return dict(path=path, sha256=hashlib.sha256(read_captured(args.root, path)).hexdigest())
        record = prepare_record(args.root, args.lcsc, blob(args.catalog), blob(args.datasheets), args.datasheet_id)
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
