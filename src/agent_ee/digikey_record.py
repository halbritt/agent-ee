"""Offline replay of ``agent-ee.digikey-capture/1``: parser, validator and Stage 2 projection.

Split from the owner-run DigiKey producer (AGENTEE-254) so that checks can
replay a captured DigiKey record without importing any request, credential or
child-process code. Nothing here opens a socket, reads the environment or
writes a file: every summary field is re-derived from retained response bytes.
"""
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
import urllib.parse

from .storage import canonical

SCHEMA = 'agent-ee.digikey-capture/1'
PARSER = 'agent-ee.digikey-product-v4/1'
IDENTITY_SCHEMA = 'agent-ee.external-part-identity/1'
MANIFEST = 'digikey-capture.json'
DEFAULT_BASE_URL = 'https://api.digikey.com'
TOKEN_PATH = '/v1/oauth2/token'
PRODUCT_PATH = '/products/v4/search/{query}/productdetails'
KEYWORD_PATH = '/products/v4/search/keyword'
KEYWORD_LIMIT = 10
LOCALE = {'language': 'en', 'currency': 'USD', 'site': 'US'}
LOOPBACK_HOSTS = frozenset(('127.0.0.1', '::1', 'localhost'))
KINDS = ('product', 'keyword')
KEY_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
MAX_QUERIES = 100
MAX_QUERY_CHARS = 250
MAX_BYTES = 4 * 1024 * 1024
MIN_INTERVAL = 0.5
STATUSES = ('found', 'not_found', 'failed')
# Exact DigiKey parameter names read into summary fields; everything else stays raw.
PACKAGE_PARAMETER = 'Package / Case'
SEATED_HEIGHT_PARAMETER = 'Height - Seated (Max)'
OPERATING_TEMPERATURE_PARAMETER = 'Operating Temperature'
CAPTURE_KEYS = frozenset(('schema', 'as_of', 'completed_at', 'source', 'token_exchanges', 'requests', 'parts'))
SOURCE_KEYS = frozenset(('base_url', 'token_path', 'product_path', 'keyword_path', 'keyword_body',
                         'locale', 'parser', 'min_interval_seconds'))
TOKEN_KEYS = frozenset(('fetched_at', 'http_status', 'expires_in'))
REQUEST_KEYS = frozenset(('key', 'kind', 'query', 'fetched_at', 'status', 'http_status', 'response', 'reason'))
RESPONSE_KEYS = frozenset(('path', 'sha256', 'size_bytes'))
PART_KEYS = ('query', 'mpn', 'manufacturer', 'digikey_part_number', 'package', 'primary_datasheet_url',
             'quantity_available', 'unit_price', 'product_status', 'seated_height',
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


def _derive(product, query):
    if not isinstance(product, dict):
        raise ValueError('Product must be an object')
    manufacturer = product.get('Manufacturer')
    if not isinstance(manufacturer, dict):
        raise ValueError('Manufacturer must be an object')
    quantity = product.get('QuantityAvailable')
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        raise ValueError('QuantityAvailable must be a non-negative integer')
    price = product.get('UnitPrice')
    if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float)) or
                              not math.isfinite(price) or price < 0):
        raise ValueError('UnitPrice must be a finite non-negative number or null')
    status = product.get('ProductStatus')
    if status is not None and not isinstance(status, dict):
        raise ValueError('ProductStatus must be an object or null')
    parameters = _parameters(product.get('Parameters'))
    return {'query': query,
            'mpn': _text(product.get('ManufacturerProductNumber'), 'ManufacturerProductNumber'),
            'manufacturer': _text(manufacturer.get('Name'), 'Manufacturer.Name'),
            'digikey_part_number': _variation(product.get('ProductVariations'), query),
            'package': _named(parameters, PACKAGE_PARAMETER),
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
        raise ValueError(f'{key}: DigiKey record has no {PACKAGE_PARAMETER!r}; an external identity needs a package')
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
    number = part['digikey_part_number'] or 'without a DigiKey part number'
    purchase = (f'DigiKey {number} for {part["manufacturer"]} {part["mpn"]}; captured {record["fetched_at"]}: '
                f'quantity_available={part["quantity_available"]}, unit_price={part["unit_price"]}')
    return {'identity': identity,
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


# ------------------------------------------------------------- check evidence (AGENTEE-254)

EVIDENCE_KEYS = frozenset(('capture', 'capture_sha256', 'parts'))
INCH_MM = Decimal('25.4')
HEIGHT_RE = re.compile(r'(?:([0-9]+(?:\.[0-9]+)?)" \()?([0-9]+(?:\.[0-9]+)?)mm(?(1)\))')
TEMPERATURE_RE = re.compile(r'(-?[0-9]+(?:\.[0-9]+)?)°C ~ (-?[0-9]+(?:\.[0-9]+)?)°C(?: \((TA|TJ|TC)\))?')
# Only an unqualified or ambient (TA) range is compared with a declared ambient;
# junction (TJ) and case (TC) limits depend on dissipation and stay unknown.
AMBIENT_QUALIFIERS = (None, 'TA')


def evidence_declaration(raw, label):
    """Validate ``{capture, capture_sha256, parts: {reference: key}}`` naming one captured manifest."""
    from .candidate import safe_relative
    if not isinstance(raw, dict) or set(raw) != EVIDENCE_KEYS:
        raise ValueError(f'{label} requires exactly {sorted(EVIDENCE_KEYS)}')
    capture = raw['capture']
    if (not isinstance(capture, str) or str(safe_relative(capture)) != capture or
            PurePosixPath(capture).name != MANIFEST):
        raise ValueError(f'{label}.capture must be a normalized captured path to {MANIFEST}')
    if not isinstance(raw['capture_sha256'], str) or not SHA256_RE.fullmatch(raw['capture_sha256']):
        raise ValueError(f'{label}.capture_sha256 must be a lowercase SHA-256')
    parts = raw['parts']
    if (not isinstance(parts, dict) or not parts or
            any(not isinstance(ref, str) or not ref.strip() or ref != ref.strip() or
                not isinstance(key, str) or not KEY_RE.fullmatch(key) for ref, key in parts.items())):
        raise ValueError(f'{label}.parts must map trimmed references to DigiKey capture keys')
    return raw


def evidence_inputs(raw, inputs=None):
    """Capture manifest plus the retained responses it names, for immutable scope capture.

    Without readable inputs only the manifest is returned; replay then refuses
    any response that was not captured.
    """
    paths = [raw['capture']]
    if inputs is None:
        return paths
    from .catalog import strict_json
    from .parts_manifest import read_captured
    try:
        doc = strict_json(read_captured(inputs, raw['capture']).decode('utf-8'))
        records = doc['requests'] if isinstance(doc, dict) and isinstance(doc.get('requests'), list) else []
    except (OSError, UnicodeDecodeError, ValueError):
        return paths
    parent = PurePosixPath(raw['capture']).parent
    for record in records:
        response = record.get('response') if isinstance(record, dict) else None
        if isinstance(response, dict) and isinstance(response.get('path'), str):
            paths.append((parent / response['path']).as_posix())
    return paths


def replay_evidence(root, raw, files=None):
    """Replay a declared capture offline; every read file must equal its pinned or captured hash.

    Returns ``(doc, dependencies)``. ``files`` (the candidate manifest's
    path -> sha256 map) binds the retained responses to the captured scope.
    """
    doc, _, dependencies = load_capture(root, raw['capture'], allow_failed=True)
    if dependencies[raw['capture']] != raw['capture_sha256']:
        raise ValueError(f'{raw["capture"]} differs from the declared capture_sha256')
    if files is not None:
        uncaptured = sorted(path for path, sha in dependencies.items() if files.get(path) != sha)
        if uncaptured:
            raise ValueError('DigiKey capture files are not captured inputs: ' + ', '.join(uncaptured))
    return doc, dependencies


def part_evidence(doc, raw, dependencies, key, field):
    """One found part's source text for ``field`` with its capture/response provenance.

    Raises ValueError when the key was not requested, not found or failed;
    a ``None`` value means DigiKey listed no such parameter. Stock and price
    are deliberately not returned: DigiKey stock is never JLCPCB stock.
    """
    parameter = {'seated_height': SEATED_HEIGHT_PARAMETER,
                 'operating_temperature': OPERATING_TEMPERATURE_PARAMETER}[field]
    record = next((r for r in doc['requests'] if r['key'] == key), None)
    if record is None:
        raise ValueError(f'DigiKey capture did not request {key!r}')
    if record['status'] != 'found':
        raise ValueError(f'DigiKey request {key!r} {record["status"]}'
                         + (f': {record["reason"]}' if record['reason'] else ''))
    part = doc['parts'][key]
    response = (PurePosixPath(raw['capture']).parent / record['response']['path']).as_posix()
    return {'key': key, 'mpn': part['mpn'], 'manufacturer': part['manufacturer'],
            'parameter': parameter, 'value': part[field],
            'provenance': {'capture': {'path': raw['capture'], 'sha256': dependencies[raw['capture']]},
                           'response': {'path': response, 'sha256': record['response']['sha256']},
                           'locator': f'parts.{key}.{field}', 'fetched_at': record['fetched_at']}}


def _half_unit(number):
    return Decimal(1).scaleb(number.as_tuple().exponent) / 2


def seated_height_bounds(text):
    """Closed mm interval admitted by a DigiKey seated-height spelling; ValueError otherwise.

    ``0.039" (1.00mm)`` or ``1.00mm``. Each printed figure is read as rounded
    to its last digit, and an inch figure must agree with the mm figure.
    """
    match = HEIGHT_RE.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise ValueError(f'unparsed DigiKey seated height {text!r}')
    mm = Decimal(match[2])
    low, high = mm - _half_unit(mm), mm + _half_unit(mm)
    if match[1] is not None:
        inch = Decimal(match[1])
        low = max(low, (inch - _half_unit(inch)) * INCH_MM)
        high = min(high, (inch + _half_unit(inch)) * INCH_MM)
        if low > high:
            raise ValueError(f'DigiKey seated height {text!r} has disagreeing inch and mm figures')
    if mm <= 0:
        raise ValueError(f'DigiKey seated height {text!r} is not positive')
    return low, high


def operating_temperature_ranges(text):
    """``[(low_c, high_c, qualifier)]`` from ``-40°C ~ 85°C (TA)``-style text; ValueError otherwise."""
    if not isinstance(text, str):
        raise ValueError(f'unparsed DigiKey operating temperature {text!r}')
    ranges = []
    for segment in text.split(', '):
        match = TEMPERATURE_RE.fullmatch(segment)
        if match is None:
            raise ValueError(f'unparsed DigiKey operating temperature {text!r}')
        low, high = Decimal(match[1]), Decimal(match[2])
        if low > high:
            raise ValueError(f'DigiKey operating temperature {text!r} is an empty range')
        ranges.append((low, high, match[3]))
    if len({qualifier for _, _, qualifier in ranges}) != len(ranges):
        raise ValueError(f'DigiKey operating temperature {text!r} repeats a qualifier')
    return ranges


def ambient_verdict(text, minimum, maximum):
    """``('pass'|'defect'|'unknown', reason)`` for a declared ambient range against DigiKey text."""
    try:
        ranges = operating_temperature_ranges(text)
    except ValueError as exc:
        return 'unknown', str(exc)
    ambient = [r for r in ranges if r[2] in AMBIENT_QUALIFIERS]
    if len(ambient) != 1:
        return 'unknown', (f'DigiKey operating temperature {text!r} has no single unqualified or TA range; '
                           'junction or case limits do not establish ambient suitability')
    low, high, _ = ambient[0]
    if low <= minimum and maximum <= high:
        return 'pass', None
    return 'defect', f'declared ambient {minimum}..{maximum} degC is outside rated {low}..{high} degC'
