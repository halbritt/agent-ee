"""Explicit host-only acquisition of pinned catalog volumes; checks never import this."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

from .catalog import publish_directory, strict_json, validate_source
from .catalog_jlcparts import FORMAT
from .datasheet_capture import _Redirects, validate_url
from .storage import digest, write_json

MAX_SECONDS = 3600


def volume_url(manifest, volume):
    url = validate_url(manifest['source_url'].rstrip('/') + '/' + volume['path'])
    if urllib.parse.urlsplit(url).query:
        raise ValueError('catalog base URL cannot have a query')
    return url


def download_volumes(manifest, stage):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirects())
    for volume in manifest['volumes']:
        path = stage / volume['path']
        # No credentials, environment proxy configuration, retries or live API calls.
        with opener.open(volume_url(manifest, volume), timeout=30) as response, path.open('xb') as output:
            if response.status != 200:
                raise ValueError('catalog download requires HTTP 200')
            if response.headers.get('Content-Length') is not None and int(response.headers['Content-Length']) != volume['size_bytes']:
                raise ValueError('volume Content-Length mismatch')
            size = 0
            while block := response.read(min(1024**2, volume['size_bytes'] + 1 - size)):
                size += len(block)
                if size > volume['size_bytes']:
                    raise ValueError('volume exceeds pinned byte limit')
                output.write(block)
        if size != volume['size_bytes'] or digest(path) != volume['sha256']:
            raise ValueError('volume size/hash mismatch')


def acquire_catalog(manifest, destination, *, timeout=900):
    """Publish raw pinned inputs only after an owned, deadline-bounded fetch exits."""
    validate_source(manifest)
    if any(v['format'] != FORMAT for v in manifest['volumes']):
        raise ValueError('acquisition supports only the jlcparts split feed')
    if type(timeout) is not int or not 1 <= timeout <= MAX_SECONDS:
        raise ValueError(f'acquisition deadline must be 1..{MAX_SECONDS} seconds')
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
        command = [sys.executable, '-m', 'agent_ee.catalog_feed', '--fetch',
                   str(scratch / 'source.json'), str(stage)]
        # A process deadline also bounds DNS, redirects and trickling responses.
        with subprocess.Popen(command) as child:
            try:
                status = child.wait(timeout=timeout)
                if status:
                    raise RuntimeError(f'catalog download failed (exit {status})')
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
        for volume in manifest['volumes']:
            path = stage / volume['path']
            if path.stat().st_size != volume['size_bytes'] or digest(path) != volume['sha256']:
                raise ValueError('downloaded volume size/hash mismatch')
        write_json(stage / 'source-manifest.json', manifest)
        publish_directory(stage, destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', nargs=2, help=argparse.SUPPRESS)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args(argv)
    if args.fetch:
        manifest = strict_json(Path(args.fetch[0]).read_text(encoding='utf-8'))
        validate_source(manifest)
        download_volumes(manifest, Path(args.fetch[1]))
    elif args.manifest is not None and args.out is not None:
        acquire_catalog(strict_json(args.manifest.read_text(encoding='utf-8')), args.out, timeout=args.timeout)
    else:
        parser.error('--manifest and --out are required')


if __name__ == '__main__':
    main()
