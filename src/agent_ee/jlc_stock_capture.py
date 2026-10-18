"""Owner-run JLCPCB stock capture: one public search request per LCSC id.

Host-side preparation only, modelled on ``datasheet_capture``. It is opt-in
(``allow_network=True`` / ``--allow-network``), sends no credentials or cookies,
ignores ambient proxies and refuses redirects. Each raw response is retained
byte for byte with its SHA-256 and UTC fetch time, and the parsed observations
are published as an ``agent-ee.jlc-catalog/1`` capture that
``assembly_readiness`` consumes offline. Checks never import the network half of
this module; they call only ``parse_search_response`` and ``validate_capture``
on captured bytes.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

from .storage import canonical

SCHEMA = 'agent-ee.jlc-catalog/1'
PARSER = 'agent-ee.jlc-search-response/1'
# JLCPCB's public parts-search endpoint (jlcpcb.com/parts). Its response shape
# is qualified only by an owner-run capture; unrecognised shapes fail closed.
DEFAULT_ENDPOINT = ('https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/'
                    'smtGood/selectSmtComponentList')
USER_AGENT = 'agent-ee-jlc-stock-capture/1 (owner-run, one request per LCSC id)'
LCSC_RE = re.compile(r'^C[1-9][0-9]*$')
MAX_IDS = 256
MAX_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 60
DEFAULT_INTERVAL = 5.0
MIN_INTERVAL = 1.0
STATUSES = ('found', 'not_found', 'failed')
TIERS = {'base': 'Basic', 'expand': 'Extended'}
CAPTURE_KEYS = frozenset(('schema', 'as_of', 'completed_at', 'stale_before', 'source', 'requests', 'parts'))
SOURCE_KEYS = frozenset(('endpoint', 'method', 'request_body', 'parser', 'min_interval_seconds'))
REQUEST_KEYS = frozenset(('lcsc', 'fetched_at', 'status', 'http_status', 'response', 'reason'))
RESPONSE_KEYS = frozenset(('path', 'sha256', 'size_bytes'))
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')


def request_body(lcsc):
    """The exact JSON body sent for one LCSC id; retained in the capture source."""
    return canonical({'currentPage': 1, 'keyword': lcsc, 'pageSize': 25})


def _strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError(f'duplicate JSON key {key!r}')
            out[key] = value
        return out

    def constant(value):
        raise ValueError(f'non-JSON numeric constant {value}')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def _optional_text(item, key):
    value = item.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f'{key} must be a string or null')
    return (value or '').strip()


def _price1(prices):
    if prices is None:
        return None
    if not isinstance(prices, list):
        raise ValueError('componentPrices must be a list or null')
    tiers = []
    for tier in prices:
        if not isinstance(tier, dict):
            raise ValueError('componentPrices entries must be objects')
        start, price = tier.get('startNumber'), tier.get('productPrice')
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ValueError('componentPrices startNumber must be a non-negative integer')
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price < 0:
            raise ValueError('componentPrices productPrice must be a finite non-negative number')
        tiers.append((start, float(price)))
    if len({start for start, _ in tiers}) != len(tiers):
        raise ValueError('componentPrices repeats a startNumber')
    return min(tiers)[1] if tiers else None


def parse_search_response(raw, lcsc):
    """Return ``('found', part)`` or ``('not_found', None)``; raise ValueError otherwise.

    Only an exact ``componentCode`` match counts. Stock is JLCPCB's
    ``stockCount``; no other (for example LCSC) stock figure is read. Any
    shape not recognised here is refused instead of guessed.
    """
    if not isinstance(lcsc, str) or not LCSC_RE.fullmatch(lcsc):
        raise ValueError(f'invalid LCSC id {lcsc!r}')
    try:
        doc = _strict_json(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f'response is not strict JSON: {exc}') from exc
    if not isinstance(doc, dict) or doc.get('code') != 200 or isinstance(doc.get('code'), bool):
        raise ValueError('response code is not 200')
    data = doc.get('data')
    page = data.get('componentPageInfo') if isinstance(data, dict) else None
    if not isinstance(page, dict) or 'list' not in page:
        raise ValueError('response lacks data.componentPageInfo.list')
    rows = page['list'] if page['list'] is not None else []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('data.componentPageInfo.list must be a list of objects')
    matches = [row for row in rows if row.get('componentCode') == lcsc]
    if not matches:
        return 'not_found', None
    if len(matches) > 1:
        raise ValueError(f'{len(matches)} rows claim componentCode {lcsc}')
    row = matches[0]
    stock = row.get('stockCount')
    if isinstance(stock, bool) or not isinstance(stock, int) or stock < 0:
        raise ValueError('stockCount must be a non-negative integer')
    library = row.get('componentLibraryType')
    if library not in TIERS:
        raise ValueError(f'unsupported componentLibraryType {library!r}')
    preferred = row.get('preferredComponentFlag', False)
    if not isinstance(preferred, bool):
        raise ValueError('preferredComponentFlag must be boolean')
    tier = 'Preferred' if library == 'expand' and preferred else TIERS[library]
    return 'found', {'lcsc': lcsc, 'stock': stock, 'library_tier': tier,
                     'mfr': _optional_text(row, 'componentBrandEn'),
                     'mpn': _optional_text(row, 'componentModelEn'),
                     'package': _optional_text(row, 'componentSpecificationEn'),
                     'category': _optional_text(row, 'firstSortName'),
                     'price1': _price1(row.get('componentPrices'))}


def _utc(value, label):
    if not isinstance(value, str) or not value.endswith('+00:00'):
        raise ValueError(f'{label} must be an ISO-8601 UTC timestamp')
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f'{label} must be an ISO-8601 UTC timestamp') from exc
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds():
        raise ValueError(f'{label} must be an ISO-8601 UTC timestamp')
    return stamp


def is_capture(doc):
    """A producer capture carries its request list; looser jlc-catalog/1 files do not."""
    return isinstance(doc, dict) and doc.get('schema') == SCHEMA and 'requests' in doc


def response_paths(doc):
    """Relative paths of retained responses, for immutable-input declaration."""
    return sorted(r['response']['path'] for r in doc.get('requests', [])
                  if isinstance(r, dict) and isinstance(r.get('response'), dict)
                  and isinstance(r['response'].get('path'), str))


def validate_capture(doc, read_response):
    """Validate a producer capture and re-derive every part from its raw bytes.

    ``read_response(rel_path)`` returns the retained bytes (the caller resolves
    paths inside captured inputs). Returns ``{as_of, completed_at, requested,
    not_found, failed}``. The summary ``parts`` must equal the re-parse exactly.
    """
    if not is_capture(doc) or set(doc) != CAPTURE_KEYS:
        raise ValueError(f'{SCHEMA} capture requires exactly {sorted(CAPTURE_KEYS)}')
    as_of = _utc(doc['as_of'], 'as_of')
    completed = _utc(doc['completed_at'], 'completed_at')
    if completed < as_of:
        raise ValueError('completed_at precedes as_of')
    if doc['stale_before'] is not None:
        raise ValueError('a producer capture declares no stale_before; the run declares staleness')
    source = doc['source']
    if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
        raise ValueError(f'source requires exactly {sorted(SOURCE_KEYS)}')
    if (source['method'] != 'POST' or source['parser'] != PARSER or
            source['request_body'] != request_body('{lcsc}').decode()):
        raise ValueError('source method, parser or request body is not the producer contract')
    from .datasheet_capture import validate_url
    validate_url(source['endpoint'])
    interval = source['min_interval_seconds']
    if (isinstance(interval, bool) or not isinstance(interval, (int, float)) or
            not math.isfinite(interval) or interval < MIN_INTERVAL):
        raise ValueError(f'min_interval_seconds must be >= {MIN_INTERVAL}')
    requests = doc['requests']
    if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_IDS:
        raise ValueError(f'requests must list 1..{MAX_IDS} LCSC ids')
    derived, requested, not_found, failed = {}, [], [], {}
    for record in requests:
        if not isinstance(record, dict) or set(record) != REQUEST_KEYS:
            raise ValueError(f'request record requires exactly {sorted(REQUEST_KEYS)}')
        lcsc, status, response = record['lcsc'], record['status'], record['response']
        if not isinstance(lcsc, str) or not LCSC_RE.fullmatch(lcsc) or lcsc in requested:
            raise ValueError(f'request LCSC ids must be valid and unique: {lcsc!r}')
        requested.append(lcsc)
        fetched = _utc(record['fetched_at'], f'{lcsc}.fetched_at')
        if not as_of <= fetched <= completed:
            raise ValueError(f'{lcsc}.fetched_at lies outside as_of..completed_at')
        if status not in STATUSES:
            raise ValueError(f'{lcsc} has unsupported status {status!r}')
        http_status = record['http_status']
        if http_status is not None and (isinstance(http_status, bool) or not isinstance(http_status, int)):
            raise ValueError(f'{lcsc}.http_status must be an integer or null')
        if (record['reason'] is None) != (status != 'failed') or (
                record['reason'] is not None and not isinstance(record['reason'], str)):
            raise ValueError(f'{lcsc}: exactly failed requests carry a reason')
        if response is None:
            if status != 'failed':
                raise ValueError(f'{lcsc}: {status} requires a retained response')
            failed[lcsc] = record['reason']
            continue
        if not isinstance(response, dict) or set(response) != RESPONSE_KEYS:
            raise ValueError(f'{lcsc}.response requires exactly {sorted(RESPONSE_KEYS)}')
        if response['path'] != f'responses/{lcsc}.json':
            raise ValueError(f'{lcsc}.response.path must be responses/{lcsc}.json')
        raw = read_response(response['path'])
        if len(raw) != response['size_bytes'] or hashlib.sha256(raw).hexdigest() != response['sha256']:
            raise ValueError(f'{lcsc} retained response does not match its size/sha256')
        if status == 'failed':
            failed[lcsc] = record['reason']
            continue
        if http_status != 200:
            raise ValueError(f'{lcsc}: {status} requires HTTP 200')
        reparsed, part = parse_search_response(raw, lcsc)
        if reparsed != status:
            raise ValueError(f'{lcsc}: recorded status {status} but response parses as {reparsed}')
        if part is None:
            not_found.append(lcsc)
        else:
            derived[lcsc] = {**part, 'last_seen': record['fetched_at']}
    if doc['parts'] != derived:
        raise ValueError('parts do not equal the observations re-derived from retained responses')
    return {'as_of': doc['as_of'], 'completed_at': doc['completed_at'],
            'requested': requested, 'not_found': not_found, 'failed': failed}


# ------------------------------------------------------------- producer (host only)

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f'redirect refused to {newurl}', headers, fp)


def _query(url, lcsc, seconds, max_bytes):
    """Child-process body: one POST; returns status, raw bytes (base64) and failure reason."""
    from .datasheet_capture import validate_url
    out = {'http_status': None, 'body': None, 'reason': None}
    try:
        validate_url(url)
        request = urllib.request.Request(url, data=request_body(lcsc), method='POST', headers={
            'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': USER_AGENT})
        # Ignore ambient proxy configuration; no cookie processor, no auth handler.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            response = opener.open(request, timeout=seconds)
        except urllib.error.HTTPError as exc:
            response = exc
            out['reason'] = f'HTTP {exc.code}'
        with response:
            out['http_status'] = response.status if hasattr(response, 'status') else response.code
            raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            out['reason'] = 'response byte limit exceeded'
            raw = raw[:max_bytes]
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


def _ids(lcsc_ids):
    if not isinstance(lcsc_ids, list) or not 1 <= len(lcsc_ids) <= MAX_IDS:
        raise ValueError(f'expected 1..{MAX_IDS} LCSC ids; this producer does no bulk crawl')
    if any(not isinstance(i, str) or not LCSC_RE.fullmatch(i) for i in lcsc_ids):
        raise ValueError('LCSC ids must match ^C[1-9][0-9]*$')
    if len(set(lcsc_ids)) != len(lcsc_ids):
        raise ValueError('LCSC ids must be unique')
    return sorted(lcsc_ids, key=lambda i: int(i[1:]))


def ids_from_netlist(netlist_xml):
    """Distinct valid LCSC ids of populated components in a native KiCad XML netlist."""
    from .assembly_readiness import _lookup_single_property, read_netlist_components
    values = {_lookup_single_property(c['properties'], 'LCSC')
              for c in read_netlist_components(netlist_xml)['populated']}
    return _ids(sorted(v for v in values if v and LCSC_RE.fullmatch(v)))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _capture(ids, stage, endpoint, timeout, max_bytes, interval):
    from .datasheet_capture import run_owned_child
    (stage / 'responses').mkdir()
    requests, parts, last_end = [], {}, None
    for lcsc in ids:
        # Pause from the end of the previous exchange, so the endpoint sees at
        # least ``interval`` seconds between consecutive requests.
        if last_end is not None:
            time.sleep(max(0.0, interval - (time.monotonic() - last_end)))
        fetched_at = _now()
        command = [sys.executable, '-m', 'agent_ee.jlc_stock_capture', '--fetch',
                   endpoint, lcsc, str(timeout), str(max_bytes)]
        stdout = run_owned_child(command, timeout)
        last_end = time.monotonic()
        result = (json.loads(stdout) if stdout is not None else
                  {'http_status': None, 'body': None, 'reason': 'fetch deadline exceeded'})
        record = {'lcsc': lcsc, 'fetched_at': fetched_at, 'status': 'failed',
                  'http_status': result['http_status'], 'response': None, 'reason': result['reason']}
        if result['body'] is not None:
            raw = base64.b64decode(result['body'], validate=True)
            rel = f'responses/{lcsc}.json'
            with (stage / rel).open('xb') as stream:
                stream.write(raw)
            record['response'] = {'path': rel, 'sha256': hashlib.sha256(raw).hexdigest(),
                                  'size_bytes': len(raw)}
            if record['reason'] is None and record['http_status'] != 200:
                record['reason'] = f"HTTP {record['http_status']}"
            if record['reason'] is None:
                try:
                    record['status'], part = parse_search_response(raw, lcsc)
                except ValueError as exc:
                    record['reason'] = f'unrecognised response: {exc}'
                else:
                    if part is not None:
                        parts[lcsc] = {**part, 'last_seen': fetched_at}
        elif record['reason'] is None:
            record['reason'] = 'no response'
        requests.append(record)
    return requests, parts


def capture_jlc_stock(lcsc_ids, out, *, allow_network=False, endpoint=DEFAULT_ENDPOINT,
                      min_interval=DEFAULT_INTERVAL, timeout=30, max_bytes=MAX_BYTES):
    """Owner-run capture into a new directory; request failures are recorded, never dropped.

    The directory (``catalog.json`` plus ``responses/``) is published by one
    rename after every request. Never call this from a check or campaign writer.
    """
    if allow_network is not True:
        raise PermissionError('JLC stock capture uses the network; pass allow_network=True (owner opt-in)')
    from .datasheet_capture import validate_url
    validate_url(endpoint)
    ids = _ids(lcsc_ids)
    _limits(timeout, max_bytes, min_interval)
    out = Path(out).absolute()
    if not out.name or out.name in ('.', '..') or out.parent.resolve() != out.parent:
        raise ValueError('output parent must be a real contained directory, without symlinks or traversal')
    if out.exists() or out.is_symlink():
        raise FileExistsError(out)
    out.mkdir()
    reservation = out.stat().st_ino
    stage, published = None, False
    try:
        stage = Path(tempfile.mkdtemp(prefix='.jlc-stock-', dir=out.parent))
        as_of = _now()
        requests, parts = _capture(ids, stage, endpoint, timeout, max_bytes, min_interval)
        doc = {'schema': SCHEMA, 'as_of': as_of, 'completed_at': _now(), 'stale_before': None,
               'source': {'endpoint': endpoint, 'method': 'POST',
                          'request_body': request_body('{lcsc}').decode(), 'parser': PARSER,
                          'min_interval_seconds': float(min_interval)},
               'requests': requests, 'parts': parts}
        validate_capture(doc, lambda rel: (stage / PurePosixPath(rel)).read_bytes())
        with (stage / 'catalog.json').open('xb') as stream:
            stream.write(canonical(doc) + b'\n')
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '--fetch':
        _, url, lcsc, seconds, max_bytes = argv
        seconds, max_bytes = float(seconds), int(max_bytes)
        _limits(seconds, max_bytes, MIN_INTERVAL)
        print(json.dumps(_query(url, lcsc, seconds, max_bytes)))
        return 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('ids', nargs='*', help='LCSC ids (C...); or use --ids-file / --netlist')
    parser.add_argument('--ids-file', help='JSON list of LCSC ids')
    parser.add_argument('--netlist', help='native KiCad XML netlist; captures every populated LCSC id')
    parser.add_argument('--out', required=True, help='new directory under an existing real parent')
    parser.add_argument('--allow-network', action='store_true',
                        help='required owner opt-in: this command contacts the endpoint')
    parser.add_argument('--endpoint', default=DEFAULT_ENDPOINT)
    parser.add_argument('--min-interval', type=float, default=DEFAULT_INTERVAL,
                        help=f'seconds from one response to the next request (default {DEFAULT_INTERVAL}, minimum {MIN_INTERVAL})')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--max-bytes', type=int, default=MAX_BYTES)
    args = parser.parse_args(argv)
    try:
        if sum(bool(x) for x in (args.ids, args.ids_file, args.netlist)) != 1:
            raise ValueError('give exactly one of: ids, --ids-file, --netlist')
        ids = (args.ids or (json.loads(Path(args.ids_file).read_text()) if args.ids_file
                            else ids_from_netlist(args.netlist)))
        doc = capture_jlc_stock(ids, args.out, allow_network=args.allow_network, endpoint=args.endpoint,
                                min_interval=args.min_interval, timeout=args.timeout,
                                max_bytes=args.max_bytes)
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')
    catalog = Path(args.out) / 'catalog.json'
    failed = [r['lcsc'] for r in doc['requests'] if r['status'] == 'failed']
    print(json.dumps({'catalog': str(catalog), 'catalog_sha256': hashlib.sha256(catalog.read_bytes()).hexdigest(),
                      'as_of': doc['as_of'], 'failed': failed}, indent=2))
    return 2 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
