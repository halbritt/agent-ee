"""Owner-run DigiKey Product Information API v4 capture for Stage 2 external parts.

Host-side preparation only, modelled on ``jlc_stock_capture``. The producer is
opt-in (``allow_network=True`` / ``--allow-network``) and needs owner-provided
OAuth 2.0 client credentials (``DIGIKEY_CLIENT_ID`` / ``DIGIKEY_CLIENT_SECRET``)
from the host environment or a private secrets file. Credentials and access
tokens reach the request child only through its stdin pipe and are never
echoed, recorded or persisted. Each raw product response is retained byte for
byte with its SHA-256 and UTC fetch time, and an ``agent-ee.digikey-capture/1``
manifest is published atomically with the responses.

The offline half (``parse_response``, ``validate_capture``, ``load_capture``,
``project_external``, ``verify_external_identity``) lives in ``digikey_record``
and is re-exported here; it never touches the network:
it re-derives every summary field from the retained bytes and projects a found
part into ``agent-ee.external-part-identity/1``, datasheet bindings for
``datasheet_capture`` and hand-fit purchase-source evidence. Checks never run
the producer; the check sandbox keeps its no-network, clear-environment rule.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from .digikey_record import (  # noqa: F401  re-exported offline half (AGENTEE-254)
    SCHEMA, PARSER, IDENTITY_SCHEMA, MANIFEST, DEFAULT_BASE_URL, TOKEN_PATH, PRODUCT_PATH, KEYWORD_PATH,
    KEYWORD_LIMIT, LOCALE, LOOPBACK_HOSTS, KINDS, KEY_RE, SHA256_RE, MAX_QUERIES, MAX_QUERY_CHARS, MAX_BYTES,
    MIN_INTERVAL, STATUSES, PACKAGE_PARAMETER, SUPPLIER_PACKAGE_PARAMETER, CONNECTOR_PARAMETERS,
    CONNECTOR_ORDER, SEATED_HEIGHT_PARAMETER, OPERATING_TEMPERATURE_PARAMETER,
    CAPTURE_KEYS, SOURCE_KEYS, TOKEN_KEYS, REQUEST_KEYS, RESPONSE_KEYS, PART_KEYS, keyword_body, _strict_json,
    base_url, _query, queries, _text, _datasheet_url, _parameters, _named, _variation, _numbers, _package, _derive,
    parse_response, _utc, _source, validate_capture, load_capture, project_external, verify_external_identity)
from .storage import canonical

USER_AGENT = 'agent-ee-digikey-capture/1 (owner-run, one request per declared part)'
CREDENTIAL_NAMES = ('DIGIKEY_CLIENT_ID', 'DIGIKEY_CLIENT_SECRET')
MAX_SECONDS = 60
MAX_RETRY_AFTER = 60
DEFAULT_INTERVAL = 1.0
TOKEN_MARGIN = 30


def _token(raw):
    """Access token and lifetime from a token response; the token is never retained."""
    doc = _strict_json(raw)
    if not isinstance(doc, dict):
        raise ValueError('token response must be a JSON object')
    token, kind, lifetime = doc.get('access_token'), doc.get('token_type'), doc.get('expires_in')
    if not isinstance(token, str) or not token or any(ord(c) <= 32 or ord(c) >= 127 for c in token):
        raise ValueError('token response lacks a usable access_token')
    if not isinstance(kind, str) or kind.casefold() != 'bearer':
        raise ValueError('token response is not a bearer token')
    if isinstance(lifetime, bool) or not isinstance(lifetime, int) or lifetime <= 0:
        raise ValueError('token expires_in must be a positive integer')
    return token, lifetime


# ------------------------------------------------------------- producer (host only)

class Credentials:
    """Owner client credentials; the representation never shows the values."""

    __slots__ = ('client_id', 'client_secret')

    def __init__(self, client_id, client_secret):
        for value in (client_id, client_secret):
            if (not isinstance(value, str) or not value or len(value) > 512 or
                    any(ord(c) <= 32 or ord(c) >= 127 for c in value)):
                raise PermissionError('DigiKey credentials are missing or malformed '
                                      f'({" / ".join(CREDENTIAL_NAMES)})')
        self.client_id, self.client_secret = client_id, client_secret

    def __repr__(self):
        return 'Credentials(<redacted>)'


def _secrets_file(path):
    """Read only the two DigiKey names from a private KEY=VALUE file owned by this user."""
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise PermissionError('DigiKey secrets file must be a regular file, not a link')
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('DigiKey secrets file must be owned by this user and not group/other accessible')
    found = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        name, sep, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if not sep or name not in CREDENTIAL_NAMES:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        if name in found:
            raise PermissionError(f'DigiKey secrets file defines {name} more than once')
        found[name] = value
    return found


def load_credentials(secrets_file=None, environ=None):
    """Credentials from a secrets file when given, else from the host environment."""
    source = _secrets_file(secrets_file) if secrets_file is not None else (
        os.environ if environ is None else environ)
    return Credentials(source.get(CREDENTIAL_NAMES[0]), source.get(CREDENTIAL_NAMES[1]))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'redirect refused', headers, fp)


def _exchange(spec):
    """Child-process body: one request described on stdin; credentials never reach argv."""
    from .datasheet_capture import validate_url
    out = {'http_status': None, 'body': None, 'reason': None, 'retry_after': None}
    try:
        validate_url(spec['url'])
        body = None if spec['body'] is None else base64.b64decode(spec['body'], validate=True)
        request = urllib.request.Request(spec['url'], data=body, method=spec['method'],
                                         headers={**spec['headers'], 'User-Agent': USER_AGENT})
        # Ignore ambient proxy configuration; no cookie processor, no redirects.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            response = opener.open(request, timeout=spec['seconds'])
        except urllib.error.HTTPError as exc:
            response = exc
            out['reason'] = f'HTTP {exc.code}'
        with response:
            out['http_status'] = response.status if hasattr(response, 'status') else response.code
            retry = response.headers.get('Retry-After') if response.headers is not None else None
            if retry is not None and retry.strip().isdigit():
                out['retry_after'] = int(retry.strip())
            raw = response.read(spec['max_bytes'] + 1)
        if len(raw) > spec['max_bytes']:
            out['reason'] = 'response byte limit exceeded'
            raw = raw[:spec['max_bytes']]
        out['body'] = base64.b64encode(raw).decode('ascii')
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as exc:
        out['reason'] = str(exc) or type(exc).__name__
    return out


def _limits(timeout, max_bytes, interval):
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
            not math.isfinite(timeout) or not 0 < timeout <= MAX_SECONDS):
        raise ValueError(f'timeout must be finite, positive and <={MAX_SECONDS} seconds per request')
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
        raise ValueError('invalid response byte limit')
    if (isinstance(interval, bool) or not isinstance(interval, (int, float)) or
            not math.isfinite(interval) or interval < MIN_INTERVAL):
        raise ValueError(f'min_interval must be >= {MIN_INTERVAL} seconds')


def _now():
    return datetime.now(timezone.utc).isoformat()


class _Session:
    """One capture's paced, bounded exchanges; holds the secrets only in memory."""

    def __init__(self, base, credentials, timeout, max_bytes, interval):
        self.base, self.credentials = base, credentials
        self.timeout, self.max_bytes, self.interval = timeout, max_bytes, interval
        self.token, self.token_until, self.next_at, self.exchanges = None, None, None, []

    def secrets(self):
        return [s for s in (self.credentials.client_id, self.credentials.client_secret, self.token) if s]

    def redact(self, text):
        for secret in self.secrets():
            text = text.replace(secret, '<redacted>')
        return text

    def send(self, method, url, headers, body):
        """Pace from the end of the previous exchange, then run one owned child."""
        from .datasheet_capture import run_owned_child
        if self.next_at is not None:
            time.sleep(max(0.0, self.next_at - time.monotonic()))
        fetched_at = _now()
        spec = {'method': method, 'url': url, 'headers': headers, 'seconds': self.timeout,
                'max_bytes': self.max_bytes,
                'body': None if body is None else base64.b64encode(body).decode('ascii')}
        command = [sys.executable, '-m', 'agent_ee.digikey_capture', '--fetch']
        try:
            stdout = run_owned_child(command, self.timeout, stdin=json.dumps(spec).encode())
        except RuntimeError as exc:
            raise RuntimeError(self.redact(str(exc))) from None
        finally:
            self.next_at = time.monotonic() + self.interval
        result = (json.loads(stdout) if stdout is not None else
                  {'http_status': None, 'body': None, 'reason': 'fetch deadline exceeded', 'retry_after': None})
        if result['retry_after'] is not None:
            # HTTP 429 (or 503) Retry-After widens the gap, bounded so a capture always ends.
            self.next_at = max(self.next_at, time.monotonic() + min(result['retry_after'], MAX_RETRY_AFTER))
        raw = None if result['body'] is None else base64.b64decode(result['body'], validate=True)
        return fetched_at, result, raw

    def authorize(self):
        """2-legged client_credentials exchange; refuses (raises) on any failure."""
        if self.token is not None and time.monotonic() < self.token_until:
            return
        form = urllib.parse.urlencode({'client_id': self.credentials.client_id,
                                       'client_secret': self.credentials.client_secret,
                                       'grant_type': 'client_credentials'}).encode()
        fetched_at, result, raw = self.send('POST', self.base + TOKEN_PATH, {
            'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}, form)
        if result['http_status'] != 200 or raw is None or result['reason'] is not None:
            reason = result['reason'] or f"HTTP {result['http_status']}"
            raise PermissionError(f'DigiKey token exchange failed: {self.redact(reason)}')
        try:
            self.token, lifetime = _token(raw)
        except ValueError as exc:
            raise PermissionError(f'DigiKey token exchange failed: {exc}') from None
        self.token_until = time.monotonic() + lifetime - min(TOKEN_MARGIN, lifetime / 2)
        self.exchanges.append({'fetched_at': fetched_at, 'http_status': 200, 'expires_in': lifetime})

    def lookup(self, item):
        self.authorize()
        headers = {'Accept': 'application/json', 'Authorization': 'Bearer ' + self.token,
                   'X-DIGIKEY-Client-Id': self.credentials.client_id,
                   'X-DIGIKEY-Locale-Language': LOCALE['language'],
                   'X-DIGIKEY-Locale-Currency': LOCALE['currency'], 'X-DIGIKEY-Locale-Site': LOCALE['site']}
        if item['kind'] == 'product':
            url = self.base + PRODUCT_PATH.format(query=urllib.parse.quote(item['query'], safe=''))
            return self.send('GET', url, headers, None)
        return self.send('POST', self.base + KEYWORD_PATH, {**headers, 'Content-Type': 'application/json'},
                         keyword_body(item['query']))


def _capture(items, stage, session):
    (stage / 'responses').mkdir()
    records, parts = [], {}
    for item in items:
        fetched_at, result, raw = session.lookup(item)
        record = {**item, 'fetched_at': fetched_at, 'status': 'failed', 'http_status': result['http_status'],
                  'response': None,
                  'reason': None if result['reason'] is None else session.redact(result['reason'])}
        if raw is not None and any(secret.encode() in raw for secret in session.secrets()):
            record['reason'], raw = 'response echoes a credential; bytes not retained', None
        if raw is not None:
            rel = f'responses/{item["key"]}.json'
            with (stage / rel).open('xb') as stream:
                stream.write(raw)
            record['response'] = {'path': rel, 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}
            if record['reason'] is None and record['http_status'] != 200:
                record['reason'] = f"HTTP {record['http_status']}"
            if record['reason'] is None:
                try:
                    record['status'], part = parse_response(raw, item['kind'], item['query'])
                except ValueError as exc:
                    record['reason'] = f'unrecognised response: {exc}'
                else:
                    if part is not None:
                        parts[item['key']] = {**part, 'last_seen': fetched_at}
        elif record['reason'] is None:
            record['reason'] = 'no response'
        records.append(record)
    return records, parts


def capture_digikey(items, out, *, allow_network=False, credentials=None, base=DEFAULT_BASE_URL,
                    min_interval=DEFAULT_INTERVAL, timeout=30, max_bytes=MAX_BYTES):
    """Owner-run capture into a new directory; request failures are recorded, never dropped.

    Refuses before any socket or output when the opt-in, the inputs or the
    credentials are missing; a failed token exchange also leaves no output.
    ``digikey-capture.json`` and ``responses/`` are published by one rename.
    Never call this from a check or campaign writer.
    """
    if allow_network is not True:
        raise PermissionError('DigiKey capture uses the network; pass allow_network=True (owner opt-in)')
    base = base_url(base)
    items = queries(items)
    _limits(timeout, max_bytes, min_interval)
    if not isinstance(credentials, Credentials):
        raise PermissionError('DigiKey capture requires owner-provided credentials (load_credentials)')
    out = Path(out).absolute()
    if not out.name or out.name in ('.', '..') or out.parent.resolve() != out.parent:
        raise ValueError('output parent must be a real contained directory, without symlinks or traversal')
    if out.exists() or out.is_symlink():
        raise FileExistsError(out)
    session = _Session(base, credentials, timeout, max_bytes, min_interval)
    as_of = _now()
    session.authorize()  # No output exists until the credentials have been accepted.
    out.mkdir()
    reservation = out.stat().st_ino
    stage, published = None, False
    try:
        stage = Path(tempfile.mkdtemp(prefix='.digikey-capture-', dir=out.parent))
        records, parts = _capture(items, stage, session)
        doc = {'schema': SCHEMA, 'as_of': as_of, 'completed_at': _now(),
               'source': {**_source(min_interval), 'base_url': base},
               'token_exchanges': session.exchanges, 'requests': records, 'parts': parts}
        validate_capture(doc, lambda rel: (stage / PurePosixPath(rel)).read_bytes(), expected=items,
                         allow_failed=True)
        manifest = canonical(doc) + b'\n'
        if any(secret.encode() in manifest for secret in session.secrets()):
            raise RuntimeError('capture manifest would record a credential; refusing to publish')
        with (stage / MANIFEST).open('xb') as stream:
            stream.write(manifest)
        if out.is_symlink() or out.stat().st_ino != reservation:
            raise RuntimeError('output reservation changed')
        stage.replace(out)
        published = True
        return doc
    finally:
        if not published:
            if stage is not None:
                shutil.rmtree(stage)
            if not out.is_symlink() and out.exists() and out.stat().st_ino == reservation:
                out.rmdir()


def _write_new_directory(out, files):
    out = Path(out).absolute()
    if not out.name or out.parent.resolve() != out.parent or out.exists() or out.is_symlink():
        raise ValueError('output must be a new directory under a real parent')
    stage = Path(tempfile.mkdtemp(prefix='.digikey-projection-', dir=out.parent))
    try:
        for name, value in files.items():
            with (stage / name).open('xb') as stream:
                stream.write(canonical(value) + b'\n')
        stage.rename(out)
    except BaseException:
        shutil.rmtree(stage)
        raise


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ['--fetch']:
        spec = json.loads(sys.stdin.buffer.read())
        _limits(spec['seconds'], spec['max_bytes'], MIN_INTERVAL)
        print(json.dumps(_exchange(spec)))
        return 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    capture = commands.add_parser('capture', help='owner-run network capture (needs --allow-network)')
    capture.add_argument('parts', nargs='*', help='product numbers (MPN or DigiKey PN); the key is the number')
    capture.add_argument('--queries-file', help='JSON list of {key, kind: product|keyword, query}')
    capture.add_argument('--out', required=True, help='new directory under an existing real parent')
    capture.add_argument('--allow-network', action='store_true',
                         help='required owner opt-in: this command contacts the DigiKey API')
    capture.add_argument('--secrets-file', help=f'private KEY=VALUE file with {" and ".join(CREDENTIAL_NAMES)} '
                         '(default: the host environment)')
    capture.add_argument('--base-url', default=DEFAULT_BASE_URL)
    capture.add_argument('--min-interval', type=float, default=DEFAULT_INTERVAL,
                         help=f'seconds from one response to the next request (default {DEFAULT_INTERVAL}, '
                              f'minimum {MIN_INTERVAL})')
    capture.add_argument('--timeout', type=float, default=30)
    capture.add_argument('--max-bytes', type=int, default=MAX_BYTES)
    validate = commands.add_parser('validate', help='offline re-derivation of a capture')
    validate.add_argument('--root', required=True)
    validate.add_argument('--capture', required=True, help=f'path of {MANIFEST} relative to --root')
    project = commands.add_parser('project', help='offline Stage 2 projection of one found part')
    project.add_argument('--root', required=True)
    project.add_argument('--capture', required=True, help=f'path of {MANIFEST} relative to --root')
    project.add_argument('--key', required=True)
    project.add_argument('--out', required=True, help='new directory for the projection files')
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate':
            _, summary, dependencies = load_capture(args.root, args.capture)
            print(json.dumps({**summary, 'dependencies': dependencies}, indent=2))
            return 0
        if args.command == 'project':
            projection = project_external(args.root, args.capture, args.key)
            _write_new_directory(args.out, {'external-identity.json': projection['identity'],
                                            'datasheet-bindings.json': [projection['datasheet_binding']],
                                            'projection.json': projection})
            print(json.dumps({'out': args.out, 'identity': projection['identity']}, indent=2))
            return 0
        if not args.allow_network:
            raise PermissionError('DigiKey capture uses the network; pass --allow-network (owner opt-in)')
        if bool(args.parts) == bool(args.queries_file):
            raise ValueError('give exactly one of: part numbers, --queries-file')
        items = (json.loads(Path(args.queries_file).read_text()) if args.queries_file else
                 [{'key': number, 'kind': 'product', 'query': number} for number in args.parts])
        doc = capture_digikey(items, args.out, allow_network=True,
                              credentials=load_credentials(args.secrets_file), base=args.base_url,
                              min_interval=args.min_interval, timeout=args.timeout, max_bytes=args.max_bytes)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, str(exc) + '\n')
    manifest = Path(args.out) / MANIFEST
    failed = [r['key'] for r in doc['requests'] if r['status'] == 'failed']
    print(json.dumps({'capture': str(manifest), 'capture_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
                      'as_of': doc['as_of'], 'failed': failed}, indent=2))
    return 2 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
