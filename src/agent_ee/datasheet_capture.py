"""Capture declared datasheet URLs before normal offline candidate capture."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from .storage import canonical, identity

MAX_PARTS = 256
MAX_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 60


def validate_url(url):
    if not isinstance(url, str) or not url or len(url) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError('invalid datasheet URL')
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError('datasheet URL requires HTTP(S), no credentials or fragment')
    parsed.port  # Validate malformed ports before any network operation.
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url, seconds, max_bytes):
    record = {'url': url, 'final_url': None, 'fetched_at': datetime.now(timezone.utc).isoformat(),
              'sha256': None, 'path': None, 'missing': True, 'reason': None}
    try:
        validate_url(url)
        # Ignore ambient proxy credentials/configuration; only explicit URLs travel.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirects())
        with opener.open(url, timeout=seconds) as response:
            record['final_url'] = validate_url(response.geturl())
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError('PDF byte limit exceeded')
            if not raw.startswith(b'%PDF-') or not raw.rstrip().endswith(b'%%EOF'):
                raise ValueError('response is not a complete PDF envelope')
        record.update(missing=False, sha256=hashlib.sha256(raw).hexdigest())
        return {'record': record, 'pdf': base64.b64encode(raw).decode('ascii')}
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as exc:
        record['reason'] = str(exc)
        return {'record': record, 'pdf': None}


def _process_identity(pid):
    proc = Path('/proc') / str(pid)
    starttime = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    try:
        cwd = os.readlink(proc / 'cwd')
    except FileNotFoundError:
        cwd = None  # An exited, unreaped child no longer exposes its cwd.
    return starttime, cwd


def _bounded_fetch(url, seconds, max_bytes):
    # A socket timeout alone cannot bound DNS, redirects or a trickling response.
    # This owned child has no tool/check sandbox and runs only the capture fetch.
    command = [sys.executable, '-m', 'agent_ee.datasheet_capture', '--fetch',
               url, str(seconds), str(max_bytes)]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        owner = _process_identity(child.pid)
        try:
            stdout, stderr = child.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            if child.poll() is None:
                if _process_identity(child.pid) != owner:
                    raise RuntimeError('capture child ownership changed; refusing cleanup')
                child.kill()
            child.communicate()
            return {'record': {'url': url, 'final_url': None,
                    'fetched_at': datetime.now(timezone.utc).isoformat(), 'sha256': None,
                    'path': None, 'missing': True, 'reason': 'fetch deadline exceeded'}, 'pdf': None}
        if child.returncode:
            raise RuntimeError(f'capture fetch worker failed ({child.returncode}): {stderr.decode(errors="replace")}')
        return json.loads(stdout)


def _parts(parts):
    if not isinstance(parts, list) or not parts or len(parts) > MAX_PARTS:
        raise ValueError(f'expected 1..{MAX_PARTS} part bindings')
    seen = set()
    for part in parts:
        if not isinstance(part, dict) or set(part) != {'id', 'datasheet_url'}:
            raise ValueError('part binding requires exactly id and datasheet_url')
        part_id = part['id']
        if not isinstance(part_id, str) or not part_id.strip() or part_id in seen:
            raise ValueError('part IDs must be nonempty and unique')
        seen.add(part_id)
        if part['datasheet_url'] is not None and not isinstance(part['datasheet_url'], str):
            raise ValueError('datasheet_url must be a string or null')
    return sorted(parts, key=lambda part: part['id'])


def bindings_from_native(native_xml, datasheet_properties):
    """Join explicit Datasheet properties to the existing full native inventory.

    Missing properties remain missing; unknown references cannot silently replace
    native components. This does not parse schematics or infer part applicability.
    """
    from .native_netlist import read_native_inventory
    components = read_native_inventory(native_xml)['components']
    if not isinstance(datasheet_properties, dict) or set(datasheet_properties) - components.keys():
        raise ValueError('datasheet properties must name known native references')
    bindings = []
    for ref in sorted(components):
        properties = datasheet_properties.get(ref, {})
        if not isinstance(properties, dict) or set(properties) - {'Datasheet'}:
            raise ValueError('expected explicit Datasheet property only')
        bindings.append({'id': ref, 'datasheet_url': properties.get('Datasheet')})
    return _parts(bindings)


def _limits(timeout, max_bytes):
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
            not math.isfinite(timeout) or not 0 < timeout <= MAX_SECONDS):
        raise ValueError('timeout must be finite, positive and <=60 seconds per URL')
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
        raise ValueError('invalid PDF byte limit')


def _capture_parts(parts, stage, timeout, max_bytes):
    records, fetched = [], {}
    for part in parts:
        url = part['datasheet_url']
        if url not in fetched:
            if not url:
                fetched[url] = {'url': url, 'final_url': None, 'fetched_at': None,
                                'sha256': None, 'path': None, 'missing': True, 'reason': 'no datasheet URL'}
            else:
                response = _bounded_fetch(url, timeout, max_bytes)
                record = response['record']
                if not record['missing']:
                    filename = hashlib.sha256(url.encode()).hexdigest() + '.pdf'
                    with (stage / filename).open('xb') as stream:
                        stream.write(base64.b64decode(response['pdf'], validate=True))
                    record['path'] = filename
                fetched[url] = record
        records.append({'id': part['id'], **fetched[url]})
    return records


def capture_datasheets(parts, out, *, timeout=30, max_bytes=MAX_BYTES):
    """New output only; URL failures are missing, infrastructure/write failures raise.

    Completed PDFs and a per-part manifest are published together by directory
    rename. Never call this preparation API from a check or campaign writer.
    """
    parts = _parts(parts)
    _limits(timeout, max_bytes)
    out = Path(out).absolute()
    if not out.name or out.name in ('.', '..') or out.parent.resolve() != out.parent:
        raise ValueError('output parent must be a real contained directory, without symlinks or traversal')
    if out.exists() or out.is_symlink():
        raise FileExistsError(out)
    # Reserve the destination so an existing empty directory is never overwritten.
    out.mkdir()
    reservation = out.stat().st_ino
    stage = None
    published = False
    try:
        stage = Path(tempfile.mkdtemp(prefix='.datasheet-', dir=out.parent))
        records = _capture_parts(parts, stage, timeout, max_bytes)
        manifest = {'schema': 'agent-ee.datasheet-capture/1', 'bindings_sha256': identity(parts),
                    'outcome': 'missing' if any(p['missing'] for p in records) else 'captured',
                    'parts': records}
        with (stage / 'manifest.json').open('xb') as stream:
            stream.write(canonical(manifest) + b'\n')
        if out.is_symlink() or out.stat().st_ino != reservation:
            raise RuntimeError('output reservation changed')
        stage.replace(out)
        published = True
        return manifest
    finally:
        if not published:
            if stage is not None:
                shutil.rmtree(stage)
            if not out.is_symlink() and out.exists() and out.stat().st_ino == reservation:
                out.rmdir()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '--fetch':
        _, url, seconds, max_bytes = argv
        seconds, max_bytes = float(seconds), int(max_bytes)
        _limits(seconds, max_bytes)
        print(json.dumps(_fetch(url, seconds, max_bytes)))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bindings', help='JSON list of {id, datasheet_url}; explicit part-binding properties')
    parser.add_argument('--native-inventory', help='native XML inventory; bindings then maps references to Datasheet properties')
    parser.add_argument('--out', required=True, help='new directory under an existing real parent')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--max-bytes', type=int, default=MAX_BYTES)
    args = parser.parse_args(argv)
    try:
        bindings = json.loads(Path(args.bindings).read_text())
        if args.native_inventory:
            bindings = bindings_from_native(args.native_inventory, bindings)
        manifest = capture_datasheets(bindings, args.out,
                                      timeout=args.timeout, max_bytes=args.max_bytes)
        return 2 if manifest['outcome'] == 'missing' else 0
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
