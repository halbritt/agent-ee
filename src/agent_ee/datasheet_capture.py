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
        request = urllib.request.Request(url, headers={
            'User-Agent': 'agent-ee/1 (public datasheet acquisition)'})
        with opener.open(request, timeout=seconds) as response:
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


def run_owned_child(command, seconds, stdin=None):
    """Run one owned capture child under a hard elapsed-time deadline.

    Returns its stdout, or None when the deadline expired (the child is then
    killed and reaped). A nonzero exit raises. The child has no tool/check
    sandbox; only host-side capture producers call this. ``stdin`` bytes, when
    given, reach the child through a pipe only (never its argv or environment).
    """
    streams = {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE}
    if stdin is not None:
        streams['stdin'] = subprocess.PIPE
    with subprocess.Popen(command, **streams) as child:
        owner = None
        try:
            owner = _process_identity(child.pid)
            try:
                stdout, stderr = (child.communicate(timeout=seconds) if stdin is None else
                                  child.communicate(input=stdin, timeout=seconds))
            except subprocess.TimeoutExpired:
                _stop_fetch(child, owner)
                return None
            if child.returncode:
                raise RuntimeError(f'capture fetch worker failed ({child.returncode}): {stderr.decode(errors="replace")}')
            return stdout
        except BaseException as exc:
            try:
                _stop_fetch(child, owner)
            except BaseException as cleanup_error:
                exc.add_note(f'capture child cleanup failed: {cleanup_error!r}')
            raise


def _bounded_fetch(url, seconds, max_bytes):
    # A socket timeout alone cannot bound DNS, redirects or a trickling response.
    command = [sys.executable, '-m', 'agent_ee.datasheet_capture', '--fetch',
               url, str(seconds), str(max_bytes)]
    stdout = run_owned_child(command, seconds)
    if stdout is None:
        return {'record': {'url': url, 'final_url': None,
                'fetched_at': datetime.now(timezone.utc).isoformat(), 'sha256': None,
                'path': None, 'missing': True, 'reason': 'fetch deadline exceeded'}, 'pdf': None}
    return json.loads(stdout)


def _stop_fetch(child, owner):
    if child.poll() is None:
        current = _process_identity(child.pid)
        if owner is not None and (current[0] != owner[0] or
                                  current[1] not in (owner[1], None)):
            raise RuntimeError('capture child ownership changed; refusing cleanup')
        # If initial identity lookup failed, Popen still owns this unreaped direct
        # child: its PID cannot have been reused. Recheck the worktree as well.
        if owner is None and current[1] != os.getcwd():
            raise RuntimeError('capture child worktree changed; refusing cleanup')
        child.kill()
    # Do not communicate again: it may be the operation that was cancelled.
    child.wait()


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


def bindings_from_native(native_xml, datasheet_properties=None):
    """Join explicit Datasheet properties to the existing full native inventory.

    Missing properties remain missing; unknown references cannot silently replace
    native components. This does not parse schematics or infer part applicability.
    """
    from .native_netlist import read_native_inventory
    components = read_native_inventory(native_xml)['components']
    if datasheet_properties is None:
        return _parts([{'id': ref, 'datasheet_url': c['datasheet']}
                       for ref, c in components.items()])
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


def _capture_parts(parts, stage, timeout, max_bytes, fetched=None):
    # ``fetched`` lets a host-side cascade fetch candidates one at a time into one stage.
    records, fetched = [], {} if fetched is None else fetched
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


def historical_source(root, locator, pdf_sha256):
    """Read a pinned historical source-list entry; never infer redirect or time."""
    from .catalog import exact_fields, nonempty
    from .datasheet_parts import captured_path, _hash
    from .storage import digest, read_json
    exact_fields(locator, ('path', 'sha256', 'entry'))
    note = captured_path(root, locator['path'])
    if digest(note) != _hash(locator['sha256']):
        raise ValueError('historical source note hash mismatch')
    entries = read_json(note)
    index = locator['entry']
    if (not isinstance(entries, list) or type(index) is not int or
            not 0 <= index < len(entries)):
        raise ValueError('historical source note requires a valid entry index')
    entry = entries[index]
    fields = ('file', 'sha256', 'url', 'captured_at_utc')
    if isinstance(entry, dict) and 'fetched_at' in entry:
        fields += ('fetched_at',)
    exact_fields(entry, fields)
    from .candidate import safe_relative
    safe_relative(entry['file'])
    if _hash(entry['sha256']) != _hash(pdf_sha256):
        raise ValueError('historical source note does not bind PDF hash')
    validate_url(entry['url'])
    for field in ('captured_at_utc', 'fetched_at'):
        value = entry.get(field)
        if value is None and field == 'fetched_at':
            continue
        nonempty(value)
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError('historical source timestamps require timezone')
    return entry


def import_historical_pdf(pdf, source_root, source_note, part_id, out):
    """Import retained bytes with a pinned note into a new offline acquisition."""
    from .storage import digest, write_json
    pdf = Path(pdf)
    if pdf.stat().st_size > MAX_BYTES:
        raise ValueError('PDF byte limit exceeded')
    raw = pdf.read_bytes()
    if not raw.startswith(b'%PDF-') or not raw.rstrip().endswith(b'%%EOF'):
        raise ValueError('historical import is not a complete PDF envelope')
    sha = hashlib.sha256(raw).hexdigest()
    entry = historical_source(source_root, source_note, sha)
    bindings = _parts([{'id': part_id, 'datasheet_url': entry['url']}])
    out = Path(out).absolute()
    if not out.name or out.name in ('.', '..') or out.parent.resolve() != out.parent:
        raise ValueError('output requires a real contained parent')
    out.mkdir()
    reservation = out.stat().st_ino
    stage, published = None, False
    try:
        stage = Path(tempfile.mkdtemp(prefix='.datasheet-import-', dir=out.parent))
        # Retain the exact pinned note, not a reconstructed account of the fetch.
        from .datasheet_parts import captured_path
        note_raw = captured_path(source_root, source_note['path']).read_bytes()
        if hashlib.sha256(note_raw).hexdigest() != source_note['sha256']:
            raise ValueError('historical source note changed during import')
        (stage / 'source-note.json').write_bytes(note_raw)
        (stage / (sha + '.pdf')).write_bytes(raw)
        record = dict(id=part_id, url=entry['url'], final_url=None,
            fetched_at=entry.get('fetched_at'), captured_at=entry['captured_at_utc'],
            sha256=sha, path=sha + '.pdf', missing=False, reason=None,
            acquisition_method='historical_import', redirect_provenance='unknown',
            source_note=dict(path='source-note.json', sha256=digest(stage / 'source-note.json'),
                             entry=source_note['entry']))
        manifest = dict(schema='agent-ee.datasheet-capture/2', bindings_sha256=identity(bindings),
                        outcome='imported', parts=[record])
        write_json(stage / 'manifest.json', manifest)
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


def prepare_datasheets(bindings, required_facts, out, *, timeout=30, max_bytes=MAX_BYTES):
    """Prepare source inputs only. Missing acquisitions remain explicit obligations.

    The caller owns approval, scope requirements and the subsequent ordinary
    candidate capture. This callable never starts or modifies a campaign.
    """
    from .datasheet_parts import validate_bindings
    from .storage import digest, write_json
    validate_bindings(bindings)
    refs = {p['reference'] for p in bindings['parts']}
    if not isinstance(required_facts, dict) or set(required_facts) != refs:
        raise ValueError('required facts must cover every binding reference')
    for ids in required_facts.values():
        if (not isinstance(ids, list) or not ids or
                any(not isinstance(fid, str) or not fid.strip() for fid in ids) or len(ids) != len(set(ids))):
            raise ValueError('each part needs distinct nonempty required fact IDs')
    out = Path(out).absolute()
    if out.parent.resolve() != out.parent or out.name in ('', '.', '..'):
        raise ValueError('preparation output requires a real contained parent')
    out.mkdir()
    reservation = out.stat().st_ino
    stage = None
    published = False
    try:
        stage = Path(tempfile.mkdtemp(prefix='.datasheet-preparation-', dir=out.parent))
        parts = [{'id': p['reference'], 'datasheet_url': p['datasheet_url']} for p in bindings['parts']]
        acquisition = capture_datasheets(parts, stage / 'acquisition', timeout=timeout, max_bytes=max_bytes)
        write_json(stage / 'bindings.json', bindings)
        obligations = [{k: p[k] for k in ('reference', 'selection_id', 'part_number')} |
                       {'required_facts': required_facts[p['reference']]} for p in bindings['parts']]
        write_json(stage / 'facts.json', {'schema': 'agent-ee.datasheet-facts/2',
            'bindings': {'path': 'bindings.json', 'sha256': digest(stage / 'bindings.json')},
            'acquisition': {'path': 'acquisition/manifest.json', 'sha256': digest(stage / 'acquisition/manifest.json')},
            'required_parts': obligations, 'facts': []})
        if out.is_symlink() or out.stat().st_ino != reservation:
            raise RuntimeError('preparation output reservation changed')
        stage.replace(out)
        published = True
        return {'outcome': acquisition['outcome'], 'qualification': bindings['qualification'],
                'path': str(out), 'facts': 'facts.json', 'bindings': 'bindings.json',
                'acquisition': 'acquisition/manifest.json',
                'inputs': [{'path': str(out / name), 'at': name}
                           for name in ('bindings.json', 'facts.json', 'acquisition')]}
    finally:
        if not published:
            if stage is not None:
                shutil.rmtree(stage)
            if not out.is_symlink() and out.exists() and out.stat().st_ino == reservation:
                out.rmdir()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '--import-historical':
        parser = argparse.ArgumentParser(description='Offline historical PDF import')
        parser.add_argument('--import-historical', action='store_true')
        parser.add_argument('--pdf', required=True)
        parser.add_argument('--source-root', required=True)
        parser.add_argument('--source-note', required=True, help='relative source-list JSON path')
        parser.add_argument('--source-note-sha256', required=True)
        parser.add_argument('--entry', type=int, required=True)
        parser.add_argument('--id', required=True)
        parser.add_argument('--out', required=True)
        args = parser.parse_args(argv)
        try:
            import_historical_pdf(args.pdf, args.source_root,
                dict(path=args.source_note, sha256=args.source_note_sha256, entry=args.entry),
                args.id, args.out)
            return 0
        except (OSError, ValueError) as exc:
            parser.exit(2, str(exc) + '\n')
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
