"""Explicit host-only acquisition of catalog volumes; checks never import this.

acquire_catalog fetches the volumes a source manifest records. refresh_catalog fetches the
current upstream jlcparts snapshot into the catalog cache and records which one it is.
"""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

from .catalog import (MAX_TOTAL_BYTES, MAX_VOLUME_BYTES, exact_fields, publish_directory, strict_json,
                      validate_source)
from .catalog_identity import (FORMAT as IDENTITY_FORMAT, SOURCE_SCHEMA as IDENTITY_SOURCE_SCHEMA,
                               validate_source as validate_identity_source)
from .catalog_jlcparts import FORMAT, check_archive, split_volumes
from .datasheet_capture import _Redirects, validate_url
from .storage import digest, write_json

MAX_SECONDS = 3600
FETCH_MODES = ('--fetch', '--refresh-fetch')
# jlcparts publishes its catalog as split ZIP volumes under data/ on its gh-pages branch.
JLCPARTS_REPOSITORY = 'yaqwsx/jlcparts'
JLCPARTS_BRANCH = 'gh-pages'
JLCPARTS_DATA = 'data'
GITHUB_API = 'https://api.github.com'
GITHUB_RAW = 'https://raw.githubusercontent.com'
SNAPSHOT_SCHEMA = 'agent-ee.catalog-snapshot-identity/1'
VOLUME_NAME = re.compile(r'cache\.(z[0-9]{2}|zip)')
MAX_METADATA_BYTES = 4 * 1024**2


def volume_url(manifest, volume):
    url = validate_url(manifest['source_url'].rstrip('/') + '/' + volume['path'])
    if urllib.parse.urlsplit(url).query:
        raise ValueError('catalog base URL cannot have a query')
    return url


def _opener():
    # No credentials, environment proxy configuration, retries or live API calls beyond the listing.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirects())


def download_volumes(manifest, stage):
    opener = _opener()
    for volume in manifest['volumes']:
        path = stage / volume['path']
        with opener.open(volume_url(manifest, volume), timeout=30) as response, path.open('xb') as output:
            if response.status != 200:
                raise ValueError('catalog download requires HTTP 200')
            if response.headers.get('Content-Length') is not None and int(response.headers['Content-Length']) != volume['size_bytes']:
                raise ValueError('volume Content-Length mismatch')
            size = 0
            while block := response.read(min(1024**2, volume['size_bytes'] + 1 - size)):
                size += len(block)
                if size > volume['size_bytes']:
                    raise ValueError('volume exceeds recorded byte limit')
                output.write(block)
        if size != volume['size_bytes'] or digest(path) != volume['sha256']:
            raise ValueError('volume size/hash mismatch')


def _deadline(timeout):
    if type(timeout) is not int or not 1 <= timeout <= MAX_SECONDS:
        raise ValueError(f'acquisition deadline must be 1..{MAX_SECONDS} seconds')


def run_owned_fetch(mode, request, stage, timeout):
    """Run this module's own fetch child on a request file and a stage directory.

    The program and argument shape are fixed: `python -m agent_ee.catalog_feed MODE REQUEST STAGE`
    with MODE --fetch or --refresh-fetch, so the helper cannot launch any other command. The
    process deadline also bounds DNS, redirects and trickling responses."""
    if mode not in FETCH_MODES or not isinstance(request, Path) or not isinstance(stage, Path):
        raise ValueError('run_owned_fetch runs only the catalog_feed fetch child')
    _deadline(timeout)
    command = [sys.executable, '-m', 'agent_ee.catalog_feed', mode, str(request), str(stage)]
    with subprocess.Popen(command) as child:
        try:
            status = child.wait(timeout=timeout)
            if status:
                raise RuntimeError(f'catalog download failed (exit {status})')
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


def acquire_catalog(manifest, destination, *, timeout=900):
    """Publish the recorded raw inputs only after an owned, deadline-bounded fetch exits."""
    validate_source(manifest)
    if any(v['format'] != FORMAT for v in manifest['volumes']):
        raise ValueError('acquisition supports only the jlcparts split feed')
    _deadline(timeout)
    for volume in manifest['volumes']:
        volume_url(manifest, volume)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(prefix='.catalog-download-', dir=destination.parent) as scratch:
        scratch = Path(scratch)
        stage = scratch / 'volumes'
        stage.mkdir()
        write_json(scratch / 'source.json', manifest)
        run_owned_fetch('--fetch', scratch / 'source.json', stage, timeout)
        for volume in manifest['volumes']:
            path = stage / volume['path']
            if path.stat().st_size != volume['size_bytes'] or digest(path) != volume['sha256']:
                raise ValueError('downloaded volume size/hash mismatch')
        write_json(stage / 'source-manifest.json', manifest)
        publish_directory(stage, destination)


def cache_root():
    """$AGENT_EE_CATALOG_CACHE, else ~/.cache/agent-ee/catalog (as tool_source's cache)."""
    value = os.environ.get('AGENT_EE_CATALOG_CACHE')
    cache = Path(value) if value else Path.home() / '.cache/agent-ee/catalog'
    if not cache.is_absolute():
        raise ValueError('AGENT_EE_CATALOG_CACHE must be absolute')
    return cache


def snapshot_name(snapshot_date, commit):
    return f'{snapshot_date}-{commit[:12]}'


def stale_cutoff(snapshot_date):
    """One year before the snapshot date (28 February for 29 February), as for 2026-09-14."""
    day = date.fromisoformat(snapshot_date)
    return day.replace(year=day.year - 1, day=28 if (day.month, day.day) == (2, 29) else day.day).isoformat()


def source_manifests(raw, repository, commit, snapshot_date, volumes):
    """The stocked and identity source manifests of one jlcparts snapshot: same volumes, two profiles."""
    url = f'{raw.rstrip("/")}/{repository}/{commit}/{JLCPARTS_DATA}'
    stocked = {'schema': 'agent-ee.catalog-source/1', 'snapshot_date': snapshot_date, 'source_url': url,
               'source_commit': commit, 'currency': 'USD', 'stale_before': stale_cutoff(snapshot_date),
               'volumes': [dict(volume, format=FORMAT) for volume in volumes]}
    identity = {'schema': IDENTITY_SOURCE_SCHEMA, 'snapshot_date': snapshot_date, 'source_url': url,
                'source_commit': commit, 'stale_before': stale_cutoff(snapshot_date),
                'volumes': [dict(volume, format=IDENTITY_FORMAT) for volume in volumes]}
    validate_source(stocked)
    validate_identity_source(identity)
    return stocked, identity


def _metadata(opener, url):
    request = urllib.request.Request(validate_url(url), headers={'Accept': 'application/vnd.github+json'})
    with opener.open(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError('upstream metadata requires HTTP 200')
        payload = response.read(MAX_METADATA_BYTES + 1)
    if len(payload) > MAX_METADATA_BYTES:
        raise ValueError('upstream metadata exceeds its size limit')
    return strict_json(payload)


def fetch_current(request, stage):
    """Child of refresh_catalog: resolve the branch head, list its volumes and fetch them.

    Writes resolved.json; unless the cache already holds that snapshot, also writes the volumes,
    each checked against the commit's Git blob ID and listed size, and both source manifests."""
    opener = _opener()
    api, repository = request['api'], request['repository']
    branch = _metadata(opener, f'{api}/repos/{repository}/branches/{urllib.parse.quote(request["branch"], safe="")}')
    commit = branch['commit']['sha']
    committed_at = branch['commit']['commit']['committer']['date']
    if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('upstream branch head is not a commit ID')
    moment = datetime.fromisoformat(committed_at.replace('Z', '+00:00'))
    if moment.tzinfo is None:
        raise ValueError('upstream commit time has no time zone')
    resolved = {'commit': commit, 'committed_at': committed_at,
                'snapshot_date': moment.astimezone(timezone.utc).date().isoformat()}
    write_json(stage / 'resolved.json', resolved)
    if (Path(request['snapshots']) / snapshot_name(resolved['snapshot_date'], commit)).exists():
        return
    listing = _metadata(opener, f'{api}/repos/{repository}/contents/{JLCPARTS_DATA}?ref={commit}')
    if not isinstance(listing, list):
        raise ValueError('upstream data listing is not a directory')
    entries = [entry for entry in listing if isinstance(entry, dict) and entry.get('type') == 'file'
               and isinstance(entry.get('name'), str) and VOLUME_NAME.fullmatch(entry['name'])]
    volumes = split_volumes([{'path': e['name'], 'format': FORMAT, 'size_bytes': e.get('size'), 'blob': e.get('sha')}
                             for e in entries])
    if any(type(v['size_bytes']) is not int or not 0 < v['size_bytes'] <= MAX_VOLUME_BYTES for v in volumes):
        raise ValueError('upstream volume size outside capture limit')
    if sum(v['size_bytes'] for v in volumes) > MAX_TOTAL_BYTES:
        raise ValueError('upstream catalog exceeds capture limit')
    base = f'{request["raw"]}/{repository}/{commit}/{JLCPARTS_DATA}'
    recorded = []
    for volume in volumes:
        size = volume['size_bytes']
        sha256, blob = hashlib.sha256(), hashlib.sha1(b'blob %d\0' % size)
        with opener.open(validate_url(f'{base}/{volume["path"]}'), timeout=30) as response, \
                (stage / volume['path']).open('xb') as output:
            if response.status != 200:
                raise ValueError('catalog download requires HTTP 200')
            if response.headers.get('Content-Length') is not None and int(response.headers['Content-Length']) != size:
                raise ValueError('volume Content-Length mismatch')
            got = 0
            while block := response.read(min(1024**2, size + 1 - got)):
                got += len(block)
                if got > size:
                    raise ValueError('volume exceeds its listed size')
                sha256.update(block)
                blob.update(block)
                output.write(block)
        # The listing's sha is the Git blob ID: the bytes must be the commit's, not just any response.
        if got != size or blob.hexdigest() != volume['blob']:
            raise ValueError(f'{volume["path"]} differs from the Git blob at {commit}')
        recorded.append({'path': volume['path'], 'sha256': sha256.hexdigest(), 'size_bytes': size})
    stocked, identity = source_manifests(request['raw'], repository, commit, resolved['snapshot_date'], recorded)
    write_json(stage / 'source-manifest.json', stocked)
    write_json(stage / 'identity-source-manifest.json', identity)


def snapshot_identity(directory):
    """The recomputable identity of a fetched snapshot: source, date, size and SHA-256.

    sha256 covers the volumes in order (the split archive as one stream); every volume must
    match its manifest entry, and both manifests must describe the same volumes."""
    directory = Path(directory)
    stocked = strict_json((directory / 'source-manifest.json').read_text(encoding='utf-8'))
    identity = strict_json((directory / 'identity-source-manifest.json').read_text(encoding='utf-8'))
    validate_source(stocked)
    validate_identity_source(identity)
    strip = lambda manifest: {k: v for k, v in manifest.items() if k not in ('schema', 'currency', 'volumes')}
    if (strip(stocked) != strip(identity) or
            [dict(v, format=None) for v in stocked['volumes']] != [dict(v, format=None) for v in identity['volumes']]):
        raise ValueError('stocked and identity manifests describe different snapshots')
    archive, total = hashlib.sha256(), 0
    for volume in split_volumes(stocked['volumes']):
        check, size = hashlib.sha256(), 0
        with (directory / volume['path']).open('rb') as stream:
            while block := stream.read(1024**2):
                size += len(block)
                check.update(block)
                archive.update(block)
        if size != volume['size_bytes'] or check.hexdigest() != volume['sha256']:
            raise ValueError(f'{volume["path"]} size/hash differs from its manifest')
        total += size
    return {'schema': SNAPSHOT_SCHEMA,
            'source': {'url': stocked['source_url'], 'commit': stocked['source_commit']},
            'snapshot_date': stocked['snapshot_date'], 'size_bytes': total, 'sha256': archive.hexdigest(),
            'source_manifest_sha256': digest(directory / 'source-manifest.json'),
            'identity_source_manifest_sha256': digest(directory / 'identity-source-manifest.json')}


def verify_snapshot(directory):
    """Check a cached snapshot against its own snapshot.json; return that record."""
    record = strict_json((Path(directory) / 'snapshot.json').read_text(encoding='utf-8'))
    exact_fields(record, ('schema', 'source', 'snapshot_date', 'size_bytes', 'sha256', 'source_manifest_sha256',
                          'identity_source_manifest_sha256', 'repository', 'branch', 'committed_at', 'retrieved_at'))
    current = snapshot_identity(directory)
    if any(record[key] != value for key, value in current.items()):
        raise ValueError(f'cached catalog snapshot {directory} differs from its snapshot.json')
    return record


def _resolved_snapshot(record, resolved, directory):
    """Refuse a snapshot that is not the upstream head just resolved."""
    found = (record['source']['commit'], record['snapshot_date'], record.get('committed_at', resolved['committed_at']))
    wanted = (resolved['commit'], resolved['snapshot_date'], resolved['committed_at'])
    if found != wanted:
        raise ValueError(f'{directory} holds commit {found[0]} dated {found[1]}, not the resolved upstream '
                         f'head {wanted[0]} dated {wanted[1]}')


def _importable(directory, work):
    """Refuse a snapshot whose archive layout capture cannot read."""
    stocked = strict_json((Path(directory) / 'source-manifest.json').read_text(encoding='utf-8'))
    try:
        return check_archive(stocked['volumes'], directory, work)
    except ValueError as exc:
        raise ValueError(f'upstream snapshot in {directory} cannot be imported: {exc}') from exc


def refresh_catalog(cache=None, *, timeout=900, api=GITHUB_API, raw=GITHUB_RAW,
                    repository=JLCPARTS_REPOSITORY, branch=JLCPARTS_BRANCH):
    """Fetch the current upstream jlcparts snapshot into the cache; return (directory, record).

    The snapshot goes to <cache>/jlcparts/<snapshot date>-<commit[:12]>/ with its volumes, the
    stocked (source-manifest.json) and identity (identity-source-manifest.json) source manifests
    and snapshot.json, the record of which snapshot it is. The snapshot date is the UTC date of
    the branch head's commit. A snapshot already in the cache is returned only when its volumes and
    manifests match its snapshot.json and it is the commit and date just resolved; it is not
    fetched again. Nothing is published unless every volume matches the commit's Git blob ID and
    the archive has a layout capture can read (catalog_jlcparts.check_archive)."""
    _deadline(timeout)
    validate_url(api)
    validate_url(raw)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not branch:
        raise ValueError('repository must be OWNER/NAME and branch nonempty')
    snapshots = (Path(cache) if cache is not None else cache_root()) / 'jlcparts'
    snapshots.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.catalog-refresh-', dir=snapshots) as scratch:
        scratch = Path(scratch)
        stage = scratch / 'snapshot'
        stage.mkdir()
        write_json(scratch / 'request.json', {'api': api.rstrip('/'), 'raw': raw.rstrip('/'), 'repository': repository,
                                              'branch': branch, 'snapshots': str(snapshots)})
        run_owned_fetch('--refresh-fetch', scratch / 'request.json', stage, timeout)
        resolved = strict_json((stage / 'resolved.json').read_text(encoding='utf-8'))
        destination = snapshots / snapshot_name(resolved['snapshot_date'], resolved['commit'])
        if destination.exists():
            record = verify_snapshot(destination)
            _resolved_snapshot(record, resolved, destination)
            _importable(destination, scratch)
            return destination, record
        (stage / 'resolved.json').unlink()
        record = snapshot_identity(stage)
        _resolved_snapshot(record, resolved, stage)
        _importable(stage, scratch)
        record.update(repository=f'https://github.com/{repository}', branch=branch,
                      committed_at=resolved['committed_at'],
                      retrieved_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
        write_json(stage / 'snapshot.json', record)
        publish_directory(stage, destination)
    return destination, record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--fetch', nargs=2, help=argparse.SUPPRESS)
    parser.add_argument('--refresh-fetch', nargs=2, help=argparse.SUPPRESS)
    parser.add_argument('--manifest', type=Path, help='fetch the volumes this source manifest records')
    parser.add_argument('--out', type=Path, help='new directory for --manifest volumes')
    parser.add_argument('--refresh', action='store_true',
                        help='fetch the current upstream jlcparts snapshot into the cache and print its record')
    parser.add_argument('--cache', type=Path,
                        help='catalog cache for --refresh (default $AGENT_EE_CATALOG_CACHE or ~/.cache/agent-ee/catalog)')
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args(argv)
    if args.fetch:
        manifest = strict_json(Path(args.fetch[0]).read_text(encoding='utf-8'))
        validate_source(manifest)
        download_volumes(manifest, Path(args.fetch[1]))
    elif args.refresh_fetch:
        fetch_current(strict_json(Path(args.refresh_fetch[0]).read_text(encoding='utf-8')), Path(args.refresh_fetch[1]))
    elif args.refresh:
        directory, record = refresh_catalog(args.cache, timeout=args.timeout)
        print(json.dumps({'directory': str(directory), **record}, indent=2))
    elif args.manifest is not None and args.out is not None:
        acquire_catalog(strict_json(args.manifest.read_text(encoding='utf-8')), args.out, timeout=args.timeout)
    else:
        parser.error('--refresh, or --manifest and --out, is required')


if __name__ == '__main__':
    main()
