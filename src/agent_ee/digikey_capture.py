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
``project_external``, ``verify_external_identity``) never touches the network:
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
import re
import shutil
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from .storage import canonical

SCHEMA = 'agent-ee.digikey-capture/1'
# /2 (AGENTEE-262): records carry ``package_source``; an omitted or null
# top-level QuantityAvailable derives unknown (null) stock. /1 captures refuse.
PARSER = 'agent-ee.digikey-product-v4/2'
IDENTITY_SCHEMA = 'agent-ee.external-part-identity/1'
MANIFEST = 'digikey-capture.json'
DEFAULT_BASE_URL = 'https://api.digikey.com'
TOKEN_PATH = '/v1/oauth2/token'
PRODUCT_PATH = '/products/v4/search/{query}/productdetails'
KEYWORD_PATH = '/products/v4/search/keyword'
KEYWORD_LIMIT = 10
LOCALE = {'language': 'en', 'currency': 'USD', 'site': 'US'}
USER_AGENT = 'agent-ee-digikey-capture/1 (owner-run, one request per declared part)'
CREDENTIAL_NAMES = ('DIGIKEY_CLIENT_ID', 'DIGIKEY_CLIENT_SECRET')
LOOPBACK_HOSTS = frozenset(('127.0.0.1', '::1', 'localhost'))
KINDS = ('product', 'keyword')
KEY_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
MAX_QUERIES = 100
MAX_QUERY_CHARS = 250
MAX_BYTES = 4 * 1024 * 1024
MAX_SECONDS = 60
MAX_RETRY_AFTER = 60
DEFAULT_INTERVAL = 1.0
MIN_INTERVAL = 0.5
TOKEN_MARGIN = 30
STATUSES = ('found', 'not_found', 'failed')
# Exact DigiKey parameter names read into summary fields; everything else stays raw.
PACKAGE_PARAMETER = 'Package / Case'
SUPPLIER_PACKAGE_PARAMETER = 'Supplier Device Package'
# A connector without a package parameter: these exact parameters, all required
# and quoted verbatim, form its package text; any missing one leaves it unknown.
CONNECTOR_PARAMETERS = ('Connector Type', 'Number of Positions', 'Pitch - Mating', 'Mounting Type')
# Text order; 'Number of Rows' is quoted when present and is not required.
CONNECTOR_ORDER = ('Connector Type', 'Number of Positions', 'Number of Rows', 'Pitch - Mating', 'Mounting Type')
SEATED_HEIGHT_PARAMETER = 'Height - Seated (Max)'
OPERATING_TEMPERATURE_PARAMETER = 'Operating Temperature'
CAPTURE_KEYS = frozenset(('schema', 'as_of', 'completed_at', 'source', 'token_exchanges', 'requests', 'parts'))
SOURCE_KEYS = frozenset(('base_url', 'token_path', 'product_path', 'keyword_path', 'keyword_body',
                         'locale', 'parser', 'min_interval_seconds'))
TOKEN_KEYS = frozenset(('fetched_at', 'http_status', 'expires_in'))
REQUEST_KEYS = frozenset(('key', 'kind', 'query', 'fetched_at', 'status', 'http_status', 'response', 'reason'))
RESPONSE_KEYS = frozenset(('path', 'sha256', 'size_bytes'))
PART_KEYS = ('query', 'mpn', 'manufacturer', 'digikey_part_number', 'package', 'package_source',
             'primary_datasheet_url', 'quantity_available', 'unit_price', 'product_status', 'seated_height',
             'operating_temperature', 'parameters', 'last_seen')


def keyword_body(query):
    """The exact JSON body sent for one keyword query; its template is recorded."""
    return canonical({'Keywords': query, 'Limit': KEYWORD_LIMIT, 'Offset': 0})


def _strict_json(raw):
    from .catalog import strict_json
    try:
        return strict_json(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f'response is not strict JSON: {exc}') from exc


def base_url(url):
    """HTTPS origin only (plain HTTP is admitted for loopback stubs); credentials travel to it."""
    from .datasheet_capture import validate_url
    validate_url(url)
    parsed = urllib.parse.urlsplit(url)
    if parsed.path or parsed.query:
        raise ValueError('DigiKey base URL must be an origin without path or query')
    if parsed.scheme != 'https' and parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError('DigiKey base URL must use HTTPS (plain HTTP only for a loopback stub)')
    return url


def _query(kind, query):
    if kind not in KINDS:
        raise ValueError(f'query kind must be one of {list(KINDS)}')
    if (not isinstance(query, str) or not query.strip() or query != query.strip() or
            len(query) > MAX_QUERY_CHARS or any(ord(c) < 32 or ord(c) == 127 for c in query)):
        raise ValueError(f'query must be trimmed printable text of at most {MAX_QUERY_CHARS} characters')
    return query


def queries(items):
    """Validate ``[{key, kind, query}]``; requests run in key order."""
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_QUERIES:
        raise ValueError(f'expected 1..{MAX_QUERIES} queries; this producer does no bulk crawl')
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {'key', 'kind', 'query'}:
            raise ValueError('each query requires exactly key, kind and query')
        if not isinstance(item['key'], str) or not KEY_RE.fullmatch(item['key']) or item['key'] in seen:
            raise ValueError(f'query keys must match {KEY_RE.pattern} and be unique')
        seen.add(item['key'])
        _query(item['kind'], item['query'])
    return sorted((dict(item) for item in items), key=lambda item: item['key'])


# ------------------------------------------------------------- offline parser

def _text(value, label, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonempty text')
    return value.strip()


def _datasheet_url(value):
    """Direct manufacturer link as DigiKey lists it; protocol-relative links become HTTPS."""
    from .datasheet_capture import validate_url
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ValueError('DatasheetUrl must be text or null')
    value = value.strip()
    if value.startswith('//'):
        value = 'https:' + value
    return validate_url(value)


def _parameters(raw):
    if not isinstance(raw, list):
        raise ValueError('Parameters must be a list')
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError('Parameters entries must be objects')
        value = entry.get('ValueText')
        if not isinstance(value, str):
            raise ValueError('Parameters ValueText must be text')
        out.append({'parameter': _text(entry.get('ParameterText'), 'ParameterText'), 'value': value})
    return out


def _named(parameters, name):
    values = {p['value'].strip() for p in parameters if p['parameter'] == name}
    values.discard('')
    values.discard('-')
    if len(values) > 1:
        raise ValueError(f'parameter {name!r} has conflicting values')
    return values.pop() if values else None


def _variation(variations, query):
    """The DigiKey part number queried, else the lowest-MOQ variation (ties by number)."""
    if variations is None:
        return None
    if not isinstance(variations, list):
        raise ValueError('ProductVariations must be a list or null')
    ranked = []
    for entry in variations:
        if not isinstance(entry, dict):
            raise ValueError('ProductVariations entries must be objects')
        number = _text(entry.get('DigiKeyProductNumber'), 'DigiKeyProductNumber')
        moq = entry.get('MinimumOrderQuantity')
        if moq is not None and (isinstance(moq, bool) or not isinstance(moq, int) or moq < 0):
            raise ValueError('MinimumOrderQuantity must be a non-negative integer or null')
        if number.casefold() == query.casefold():
            return number
        ranked.append((math.inf if moq is None else moq, number))
    return min(ranked)[1] if ranked else None


def _numbers(product):
    variations = product.get('ProductVariations')
    numbers = {_text(product.get('ManufacturerProductNumber'), 'ManufacturerProductNumber').casefold()}
    if isinstance(variations, list):
        numbers |= {v['DigiKeyProductNumber'].strip().casefold() for v in variations
                    if isinstance(v, dict) and isinstance(v.get('DigiKeyProductNumber'), str)}
    return numbers


def _package(parameters):
    """``(package, package_source)`` from exact parameters only; ``(None, None)`` is unknown.

    ``Package / Case`` first (a conflict there refuses, as before), then
    ``Supplier Device Package``; a part with neither yields a package text only
    when every one of ``CONNECTOR_PARAMETERS`` is present, quoting each
    ``name: value`` verbatim. A conflicting fallback parameter leaves the
    package unknown rather than refusing a response the /1 parser accepted
    with a null package. Nothing is guessed.
    """
    value = _named(parameters, PACKAGE_PARAMETER)
    if value is not None:
        return value, {'rule': 'package_case', 'parameters': [PACKAGE_PARAMETER]}

    def fallback(name):
        try:
            return _named(parameters, name), False
        except ValueError:
            return None, True
    value, conflict = fallback(SUPPLIER_PACKAGE_PARAMETER)
    if conflict:
        return None, None
    if value is not None:
        return value, {'rule': 'supplier_device_package', 'parameters': [SUPPLIER_PACKAGE_PARAMETER]}
    values = {}
    for name in CONNECTOR_ORDER:
        value, conflict = fallback(name)
        if conflict or (value is None and name in CONNECTOR_PARAMETERS):
            return None, None
        if value is not None:
            values[name] = value
    used = list(values)
    return '; '.join(f'{name}: {values[name]}' for name in used), {'rule': 'connector_parameters',
                                                                 'parameters': used}


def _derive(product, query):
    if not isinstance(product, dict):
        raise ValueError('Product must be an object')
    manufacturer = product.get('Manufacturer')
    if not isinstance(manufacturer, dict):
        raise ValueError('Manufacturer must be an object')
    # DigiKey omits the top-level QuantityAvailable on zero-stock parts: that is
    # unknown stock (null), never zero. A present value must still be valid.
    quantity = product.get('QuantityAvailable')
    if quantity is not None and (isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0):
        raise ValueError('QuantityAvailable must be a non-negative integer, or omitted/null for unknown')
    price = product.get('UnitPrice')
    if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float)) or
                              not math.isfinite(price) or price < 0):
        raise ValueError('UnitPrice must be a finite non-negative number or null')
    status = product.get('ProductStatus')
    if status is not None and not isinstance(status, dict):
        raise ValueError('ProductStatus must be an object or null')
    parameters = _parameters(product.get('Parameters'))
    package, package_source = _package(parameters)
    return {'query': query,
            'mpn': _text(product.get('ManufacturerProductNumber'), 'ManufacturerProductNumber'),
            'manufacturer': _text(manufacturer.get('Name'), 'Manufacturer.Name'),
            'digikey_part_number': _variation(product.get('ProductVariations'), query),
            'package': package,
            'package_source': package_source,
            'primary_datasheet_url': _datasheet_url(product.get('DatasheetUrl')),
            'quantity_available': quantity,
            'unit_price': None if price is None else float(price),
            'product_status': _text(status.get('Status'), 'ProductStatus.Status', optional=True) if status else None,
            'seated_height': _named(parameters, SEATED_HEIGHT_PARAMETER),
            'operating_temperature': _named(parameters, OPERATING_TEMPERATURE_PARAMETER),
            'parameters': parameters}


def parse_response(raw, kind, query):
    """Return ``('found', part)`` or ``('not_found', None)``; raise ValueError otherwise.

    A product-details response must name the queried manufacturer or DigiKey
    part number. A keyword response counts only exact (case-insensitive)
    number matches, and refuses more than one distinct product.
    """
    _query(kind, query)
    doc = _strict_json(raw)
    if not isinstance(doc, dict):
        raise ValueError('response must be a JSON object')
    if kind == 'product':
        product = doc.get('Product')
        if not isinstance(product, dict):
            raise ValueError('response lacks a Product object')
        if query.casefold() not in _numbers(product):
            raise ValueError('Product does not carry the queried part number')
        return 'found', _derive(product, query)
    rows = []
    for field in ('ExactMatches', 'Products'):
        listed = doc.get(field)
        if listed is None:
            continue
        if not isinstance(listed, list) or any(not isinstance(row, dict) for row in listed):
            raise ValueError(f'{field} must be a list of objects or null')
        rows += listed
    if 'Products' not in doc:
        raise ValueError('keyword response lacks Products')
    derived = {canonical(_derive(row, query)) for row in rows if query.casefold() in _numbers(row)}
    if not derived:
        return 'not_found', None
    if len(derived) > 1:
        raise ValueError(f'{len(derived)} distinct products match {query!r}')
    return 'found', json.loads(derived.pop())


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


# ------------------------------------------------------------- offline validator

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


def _source(interval):
    return {'base_url': None, 'token_path': TOKEN_PATH, 'product_path': PRODUCT_PATH,
            'keyword_path': KEYWORD_PATH, 'keyword_body': keyword_body('{query}').decode(),
            'locale': dict(LOCALE), 'parser': PARSER, 'min_interval_seconds': float(interval)}


def validate_capture(doc, read_response, *, expected=None, allow_failed=False, response_verdicts=None):
    """Validate a capture and re-derive every part from its retained raw bytes.

    ``read_response(rel_path)`` returns retained bytes. ``expected`` (a query
    list) must equal the recorded requests exactly when given. Failed requests
    refuse unless ``allow_failed``; the producer itself records them. Returns
    ``{as_of, completed_at, requested, found, not_found, failed, response_verdicts}``.
    """
    if response_verdicts is None:
        response_verdicts = {}
    if not isinstance(doc, dict) or doc.get('schema') != SCHEMA or set(doc) != CAPTURE_KEYS:
        raise ValueError(f'{SCHEMA} capture requires exactly {sorted(CAPTURE_KEYS)}')
    as_of = _utc(doc['as_of'], 'as_of')
    completed = _utc(doc['completed_at'], 'completed_at')
    if completed < as_of:
        raise ValueError('completed_at precedes as_of')
    source = doc['source']
    if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
        raise ValueError(f'source requires exactly {sorted(SOURCE_KEYS)}')
    base_url(source['base_url'])
    interval = source['min_interval_seconds']
    if (type(interval) is not float or not math.isfinite(interval) or interval < MIN_INTERVAL):
        raise ValueError(f'min_interval_seconds must be a float >= {MIN_INTERVAL}')
    if canonical({**source, 'base_url': None}) != canonical(_source(interval)):
        raise ValueError('source paths, body, locale or parser are not the producer contract')
    tokens = doc['token_exchanges']
    if not isinstance(tokens, list) or not tokens:
        raise ValueError('a capture records at least one token exchange')
    for exchange in tokens:
        if not isinstance(exchange, dict) or set(exchange) != TOKEN_KEYS:
            raise ValueError(f'token exchange requires exactly {sorted(TOKEN_KEYS)}')
        if not as_of <= _utc(exchange['fetched_at'], 'token fetched_at') <= completed:
            raise ValueError('token fetched_at lies outside as_of..completed_at')
        if exchange['http_status'] != 200 or type(exchange['http_status']) is not int:
            raise ValueError('a recorded token exchange succeeded with HTTP 200')
        if type(exchange['expires_in']) is not int or exchange['expires_in'] <= 0:
            raise ValueError('token expires_in must be a positive integer')
    records = doc['requests']
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_QUERIES:
        raise ValueError(f'requests must list 1..{MAX_QUERIES} queries')
    asked = queries([{k: r.get(k) for k in ('key', 'kind', 'query')} if isinstance(r, dict) else r
                     for r in records])
    if expected is not None and canonical(asked) != canonical(queries(expected)):
        raise ValueError('recorded requests differ from the expected queries')
    derived, found, not_found, failed, previous = {}, [], [], {}, as_of
    for record in records:
        if set(record) != REQUEST_KEYS:
            raise ValueError(f'request record requires exactly {sorted(REQUEST_KEYS)}')
        key, status, response = record['key'], record['status'], record['response']
        fetched = _utc(record['fetched_at'], f'{key}.fetched_at')
        if not previous <= fetched <= completed:
            raise ValueError(f'{key}.fetched_at lies outside as_of..completed_at or out of order')
        previous = fetched
        if status not in STATUSES:
            raise ValueError(f'{key} has unsupported status {status!r}')
        http_status = record['http_status']
        if http_status is not None and type(http_status) is not int:
            raise ValueError(f'{key}.http_status must be an integer or null')
        if (record['reason'] is None) != (status != 'failed') or (
                record['reason'] is not None and not isinstance(record['reason'], str)):
            raise ValueError(f'{key}: exactly failed requests carry a reason')
        if status == 'failed' and not allow_failed:
            raise ValueError(f'{key} failed and cannot be consumed: {record["reason"]}')
        if response is None:
            response_verdicts[key] = {'path': None, 'sha256': None, 'verdict': 'missing'}
            if status != 'failed':
                raise ValueError(f'{key}: {status} requires a retained response')
            failed[key] = record['reason']
            continue
        if not isinstance(response, dict) or set(response) != RESPONSE_KEYS:
            raise ValueError(f'{key}.response requires exactly {sorted(RESPONSE_KEYS)}')
        if response['path'] != f'responses/{key}.json':
            raise ValueError(f'{key}.response.path must be responses/{key}.json')
        verdict = {'path': response['path'], 'sha256': response['sha256'], 'verdict': 'unverified'}
        response_verdicts[key] = verdict
        if (not isinstance(response['sha256'], str) or not SHA256_RE.fullmatch(response['sha256']) or
                type(response['size_bytes']) is not int or not 0 <= response['size_bytes'] <= MAX_BYTES):
            raise ValueError(f'{key}.response requires a lowercase SHA-256 and bounded integer size_bytes')
        try:
            raw = read_response(response['path'])
        except (OSError, ValueError):
            verdict['verdict'] = 'missing'
            raise
        if len(raw) != response['size_bytes'] or hashlib.sha256(raw).hexdigest() != response['sha256']:
            verdict['verdict'] = 'mismatch'
            raise ValueError(f'{key} retained response does not match its size/sha256')
        verdict['verdict'] = 'verified'
        if status == 'failed':
            failed[key] = record['reason']
            continue
        if http_status != 200:
            raise ValueError(f'{key}: {status} requires HTTP 200')
        reparsed, part = parse_response(raw, record['kind'], record['query'])
        if reparsed != status:
            raise ValueError(f'{key}: recorded status {status} but response parses as {reparsed}')
        if part is None:
            not_found.append(key)
        else:
            found.append(key)
            derived[key] = {**part, 'last_seen': record['fetched_at']}
    # Byte comparison: 1 == 1.0 == True in Python, but not in the canonical summary.
    if canonical(doc['parts']) != canonical(derived):
        raise ValueError('parts do not equal the records re-derived from retained responses')
    return {'as_of': doc['as_of'], 'completed_at': doc['completed_at'], 'requested': [r['key'] for r in records],
            'found': found, 'not_found': not_found, 'failed': failed, 'response_verdicts': response_verdicts}


def load_capture(root, path, **options):
    """Read a capture under ``root`` (no links, strict JSON) and validate it offline.

    Returns ``(doc, summary, dependencies)`` where dependencies maps every
    read capture path to its SHA-256.
    """
    from .catalog import strict_json
    from .parts_manifest import read_captured
    raw = read_captured(root, path)
    if PurePosixPath(path).name != MANIFEST:
        raise ValueError(f'a DigiKey capture manifest is named {MANIFEST}')
    doc = strict_json(raw.decode('utf-8'))
    parent = PurePosixPath(path).parent
    dependencies = {path: hashlib.sha256(raw).hexdigest()}

    def read_response(rel):
        target = (parent / rel).as_posix()
        data = read_captured(root, target)
        dependencies[target] = hashlib.sha256(data).hexdigest()
        return data
    summary = validate_capture(doc, read_response, **options)
    return doc, summary, dict(sorted(dependencies.items()))


# ------------------------------------------------------------- Stage 2 adapters

def project_external(root, path, key):
    """Deterministic projection of one found part; refuses failed or unrequested items.

    Returns the ``agent-ee.external-part-identity/1`` declaration (for
    ``part_record.prepare_external_record``), the ``{id, datasheet_url}``
    binding (for ``datasheet_capture``) with its provenance, and hand-fit
    purchase-source evidence (for an assembly deviation in
    ``assembly_readiness``). Writes nothing.
    """
    from .catalog import nonempty
    doc, _, dependencies = load_capture(root, path)
    if key not in doc['parts']:
        raise ValueError(f'{key!r} has no found DigiKey record in the capture')
    part = doc['parts'][key]
    record = next(r for r in doc['requests'] if r['key'] == key)
    if part['package'] is None:
        raise ValueError(f'{key}: DigiKey record has no {PACKAGE_PARAMETER!r}, {SUPPLIER_PACKAGE_PARAMETER!r} or '
                         f'complete connector parameters {list(CONNECTOR_PARAMETERS)}; package unknown, and an '
                         'external identity needs a package')
    for field in ('mpn', 'manufacturer', 'package'):
        nonempty(part[field])
    response_path = (PurePosixPath(path).parent / record['response']['path']).as_posix()
    locator = ('Product' if record['kind'] == 'product'
               else f'Products[ManufacturerProductNumber={part["mpn"]}]')
    identity = {'schema': IDENTITY_SCHEMA, 'mpn': part['mpn'], 'manufacturer': part['manufacturer'],
                'package': part['package'],
                'evidence_refs': [{'path': path, 'sha256': dependencies[path], 'locator': f'parts.{key}'},
                                  {'path': response_path, 'sha256': record['response']['sha256'],
                                   'locator': locator}]}
    provenance = {'capture': {'path': path, 'sha256': dependencies[path]},
                  'response': {'path': response_path, 'sha256': record['response']['sha256']},
                  'key': key, 'field': 'DatasheetUrl', 'fetched_at': record['fetched_at']}
    package_provenance = {'capture': {'path': path, 'sha256': dependencies[path]},
                          'response': {'path': response_path, 'sha256': record['response']['sha256']},
                          'key': key, 'locator': f'{locator}.Parameters', **part['package_source']}
    number = part['digikey_part_number'] or 'without a DigiKey part number'
    quantity = ('unknown (QuantityAvailable not reported)' if part['quantity_available'] is None
                else part['quantity_available'])
    purchase = (f'DigiKey {number} for {part["manufacturer"]} {part["mpn"]}; captured {record["fetched_at"]}: '
                f'quantity_available={quantity}, unit_price={part["unit_price"]}')
    return {'identity': identity,
            'package_provenance': package_provenance,
            'datasheet_binding': {'id': key, 'datasheet_url': part['primary_datasheet_url']},
            'datasheet_provenance': provenance,
            'hand_fit_evidence': {'purchase_source': purchase,
                                  'evidence_refs': [{'path': response_path, 'locator': locator}]},
            'ratings': {'seated_height': part['seated_height'],
                        'operating_temperature': part['operating_temperature']},
            'dependencies': dependencies}


def verify_external_identity(root, identity):
    """Replay an identity declaration against the DigiKey capture it cites."""
    from .catalog import exact_fields
    exact_fields(identity, ('schema', 'mpn', 'manufacturer', 'package', 'evidence_refs'))
    refs = identity['evidence_refs']
    cited = [r for r in refs if isinstance(r, dict) and isinstance(r.get('path'), str)
             and PurePosixPath(r['path']).name == MANIFEST and isinstance(r.get('locator'), str)
             and r['locator'].startswith('parts.')] if isinstance(refs, list) else []
    if len(cited) != 1:
        raise ValueError('identity must cite exactly one DigiKey capture record')
    projection = project_external(root, cited[0]['path'], cited[0]['locator'][len('parts.'):])
    if canonical(projection['identity']) != canonical(identity):
        raise ValueError('external identity differs from the DigiKey capture it cites')
    return projection['dependencies']


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
