"""Pinned legacy footprint conversion: offline Git blobs, kicad-cli as a process.

Source bytes come only from a local repository's object store at one full,
exact commit, through the offline Git path of ``reference_blocks``. Working-tree
edits, staged changes and untracked files never enter a capture. The converter
is ``kicad-cli fp upgrade`` in the networkless executor sandbox; this module
writes no footprint text itself. A capture is refused unless a second, forced
conversion of the converted library reproduces every member byte for byte.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path, PurePosixPath

from . import native_run
from .footprint_library import FootprintError, index_footprints, load_document
from .reference_blocks import _git, verify_external_source
from .storage import json_bytes

REQUEST_SCHEMA = 'agent-ee.footprint-capture-request/1'
CAPTURE_SCHEMA = 'agent-ee.footprint-capture/1'
MANIFEST = 'footprint-libraries.json'
RECORD = 'capture.json'
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]*')


class CaptureError(ValueError):
    pass


def _exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise CaptureError('expected fields: ' + ', '.join(keys))


def _text(value):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise CaptureError('expected nonempty text')
    return value


def _source_path(value):
    _text(value)
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value or '\\' in value or path.suffix != '.kicad_mod':
        raise CaptureError('unsafe source footprint path: ' + value)
    if not _NAME.fullmatch(path.name):
        raise CaptureError('unqualified footprint file name: ' + value)
    return path


def validate_request(request):
    """``{schema, library, repository, commit, kicad_version, members}``; members are source paths."""
    _exact(request, ('schema', 'library', 'repository', 'commit', 'kicad_version', 'members'))
    if request['schema'] != REQUEST_SCHEMA:
        raise CaptureError('unsupported footprint capture request schema')
    if not _NAME.fullmatch(_text(request['library'])) or request['library'].endswith('.pretty'):
        raise CaptureError('library needs a plain name without .pretty')
    _text(request['repository'])
    _text(request['kicad_version'])
    if not isinstance(request['commit'], str) or re.fullmatch('[0-9a-f]{40}', request['commit']) is None:
        raise CaptureError('source commit must be a full 40-hex commit id')
    members = request['members']
    if not isinstance(members, list) or not members:
        raise CaptureError('nonempty member list required')
    names = [_source_path(m).name for m in members]
    if len(set(members)) != len(members) or len(set(names)) != len(names):
        raise CaptureError('member paths and file names must be unique')
    return request


def _committed_blob(repository, commit, path):
    # Only ordinary committed blobs; trees, links and submodules are refused.
    entry = _git(repository, 'ls-tree', '-z', commit, '--', ':(literal)' + path).split(b'\x00')
    if len(entry) != 2 or not entry[0].startswith((b'100644 blob ', b'100755 blob ')):
        raise CaptureError('source is not one regular committed file: ' + path)
    blob = entry[0].split(b'\t', 1)[0].split()[2].decode('ascii')
    data = _git(repository, 'cat-file', 'blob', blob)
    if hashlib.sha1(b'blob %d\x00' % len(data) + data).hexdigest() != blob:
        raise CaptureError('object store returned bytes that differ from the blob id: ' + path)
    return blob, data


def _run(inputs, work, argv, timeout):
    record = dict(native_run.run(native_run.Intent('probe', argv, timeout=timeout), inputs=inputs, work=work))
    if record['execution'] != 'ok':
        console = (Path(work) / 'console.log').read_text(errors='replace')[-2000:]
        raise CaptureError(f'{" ".join(argv)} {record["execution"]}: {console}')
    return dict(argv=record['argv'], exit_code=record['exit_code'], console_sha256=record['console_sha256'])


def _library(directory, names):
    """The converter's output directory must hold exactly the requested regular files."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise CaptureError('converter wrote no footprint library')
    found = {p.name: p for p in directory.iterdir()}
    if set(found) != set(names) or any(p.is_symlink() or not p.is_file() for p in found.values()):
        raise CaptureError('converter output differs from the requested members: ' + ', '.join(sorted(found)))
    return {name: found[name].read_bytes() for name in names}


def _manifest(library, converted):
    return dict(schema='agent-ee.footprint-libraries/1', complete=True, libraries=[dict(
        name=library, members=[dict(path=f'{library}.pretty/{name}', sha256=hashlib.sha256(data).hexdigest())
                               for name, data in sorted(converted.items())])])


def capture_footprints(request, repository, destination, *, timeout=120):
    """Convert pinned committed footprints into a new directory with provenance.

    ``repository`` is a local Git repository read through its object store only;
    ``request['repository']`` is the label recorded instead of the local path.
    """
    validate_request(request)
    commit = request['commit']
    try:
        pinned = _git(repository, 'rev-parse', '--verify', commit + '^{commit}').decode().strip()
    except subprocess.CalledProcessError as exc:
        raise CaptureError('source commit is not a commit in the local object store: ' + commit) from exc
    if pinned != commit:
        raise CaptureError('source commit must be full exact commit')
    target = Path(destination)
    if any(p.is_symlink() for p in (target, *target.parents)) or target.exists():
        raise CaptureError('capture destination must be new, without symlink ancestors')
    sources, legacy = [], {}
    for path in request['members']:
        blob, data = _committed_blob(repository, commit, path)
        name = PurePosixPath(path).name
        legacy[name] = data
        sources.append(dict(kind='pinned-external', repository=request['repository'], commit=commit, path=path,
                            blob=blob, sha256=hashlib.sha256(data).hexdigest()))
    with tempfile.TemporaryDirectory(prefix='agent-ee-footprint-capture-') as tmp:
        root = Path(tmp)
        source = root / 'inputs/source.pretty'
        source.mkdir(parents=True)
        for name, data in legacy.items():
            (source / name).write_bytes(data)
        for name in ('version', 'first', 'second', 'resave-inputs'):
            (root / name).mkdir()
        _run(root / 'inputs', root / 'version', ['kicad-cli', 'version'], timeout)
        version = (root / 'version/stdout.log').read_text().strip()
        if version != request['kicad_version']:
            raise CaptureError(f'kicad-cli {version!r} is not the declared {request["kicad_version"]!r}')
        first = _run(root / 'inputs', root / 'first',
                     ['kicad-cli', 'fp', 'upgrade', '--output', '/work/converted.pretty', '/candidate/source.pretty'],
                     timeout)
        converted = _library(root / 'first/converted.pretty', legacy)
        resave = root / 'resave-inputs/converted.pretty'
        resave.mkdir()
        for name, data in converted.items():
            (resave / name).write_bytes(data)
        second = _run(root / 'resave-inputs', root / 'second',
                      ['kicad-cli', 'fp', 'upgrade', '--force', '--output', '/work/resaved.pretty',
                       '/candidate/converted.pretty'], timeout)
        for name, data in _library(root / 'second/resaved.pretty', legacy).items():
            if data != converted[name]:
                raise CaptureError('second conversion is not byte-identical: ' + name)
    library = request['library']
    manifest = _manifest(library, converted)
    files = {m['path']: converted[PurePosixPath(m['path']).name] for m in manifest['libraries'][0]['members']}
    # The converted text must be a footprint the shared index qualifies.
    index = index_footprints(manifest, files)
    if index['execution'] != 'ok':
        raise CaptureError('converted footprint is not qualified: ' + json.dumps(index['findings']))
    manifest_bytes = json_bytes(manifest)
    record = dict(schema=CAPTURE_SCHEMA, library=library, kicad_version=version,
                  manifest=dict(path=MANIFEST, sha256=hashlib.sha256(manifest_bytes).hexdigest()),
                  members=[dict(path=m['path'], sha256=m['sha256'], source=s)
                           for m, s in zip(manifest['libraries'][0]['members'],
                                           sorted(sources, key=lambda s: PurePosixPath(s['path']).name))],
                  conversion=dict(first=first, second=second, second_conversion='byte-identical'),
                  limitations=['Conversion and identity only: no land-pattern dimension, datasheet '
                               'or mechanical-fit qualification; 3D model references are kept unresolved.'])
    target.mkdir(mode=0o700)
    for path, data in files.items():
        (target / path).parent.mkdir(exist_ok=True)
        (target / path).write_bytes(data)
    (target / MANIFEST).write_bytes(manifest_bytes)
    (target / RECORD).write_bytes(json_bytes(record))
    return verify_capture(target)


def verify_capture(directory, repository=None):
    """Re-check member and manifest bytes; with ``repository`` also each pinned source blob offline."""
    root = Path(directory)
    record = load_document(root / RECORD)
    _exact(record, ('schema', 'library', 'kicad_version', 'manifest', 'members', 'conversion', 'limitations'))
    if record['schema'] != CAPTURE_SCHEMA:
        raise CaptureError('unsupported footprint capture schema')
    manifest_bytes = (root / MANIFEST).read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != record['manifest']['sha256']:
        raise CaptureError('footprint manifest differs from the capture record')
    manifest = load_document(root / MANIFEST)
    members = manifest['libraries'][0]['members'] if len(manifest.get('libraries', [])) == 1 else None
    if members is None or [dict(path=m['path'], sha256=m['sha256']) for m in record['members']] != members:
        raise CaptureError('capture record members differ from the manifest')
    expected = {RECORD, MANIFEST, record['library'] + '.pretty'} | {m['path'] for m in members}
    present = {p.relative_to(root).as_posix() for p in root.rglob('*')}
    if present != expected or any(p.is_symlink() for p in root.rglob('*')):
        raise CaptureError('capture directory holds unrecorded files or links')
    files = {}
    for member in members:
        data = (root / member['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != member['sha256']:
            raise CaptureError('captured footprint changed: ' + member['path'])
        files[member['path']] = data
    if record['conversion'].get('second_conversion') != 'byte-identical':
        raise CaptureError('capture does not record a byte-identical second conversion')
    try:
        index = index_footprints(manifest, files)
    except FootprintError as exc:
        raise CaptureError(str(exc)) from exc
    if index['execution'] != 'ok':
        raise CaptureError('captured footprint is not qualified')
    sources = []
    for member in record['members']:
        source = member['source']
        if repository is None:
            sources.append(dict(path=source['path'], status='unverified', detail='No local repository given'))
        else:
            sources.append(dict(path=source['path'], **verify_external_source(source, repository)))
    status = 'verified' if repository is not None and all(s['status'] == 'verified' for s in sources) else 'unverified'
    return dict(schema='agent-ee.footprint-capture-report/1', status=status, library=record['library'],
                members=[m['path'] for m in members], sources=sources,
                footprints=[f['lib_id'] for f in index['footprints']])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    capture = commands.add_parser('capture')
    capture.add_argument('request')
    capture.add_argument('--repo', required=True, help='local Git repository; only its object store is read')
    capture.add_argument('--out', required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('directory')
    verify.add_argument('--repository', help='local Git object store for the pinned sources')
    args = parser.parse_args(argv)
    try:
        if args.command == 'capture':
            report = capture_footprints(load_document(args.request), args.repo, args.out)
            report = verify_capture(args.out, args.repo)
        else:
            report = verify_capture(args.directory, args.repository)
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(json.dumps(dict(schema='agent-ee.footprint-capture-report/1', status='refused', error=str(exc))))
        return 2
    except Exception as exc:
        traceback.print_exc()
        print(json.dumps(dict(schema='agent-ee.footprint-capture-report/1', status='error',
                              error=type(exc).__name__ + ': ' + str(exc))))
        return 3
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'verified' else 1


if __name__ == '__main__':
    sys.exit(main())
