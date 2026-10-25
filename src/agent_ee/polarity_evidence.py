"""Supplemental, source-bound component polarity evidence for `cpl_rotation` (AGENTEE-524 D1).

A captured `agent-ee.component-polarity/1` document records, for one exact
supplier capture, which supplier symbol pin numbers are anode and cathode, the
manufacturer source that defines the polarity convention, and the native
supplier feature ids that carry it (for example the cathode band tracks and
the symbol bar). It is interpreted component data, not a per-reference
override: it supplies a polarity only where the captured supplier symbol names
none, and a named symbol polarity or a board mapping that contradicts it is a
defect. Every identity and digest it declares is verified against the same
captured prerequisites the check already uses. Missing evidence stays unknown;
malformed or stale evidence refuses the check.
"""

import re

from .easyeda_footprint import SupplierError, symbol_pins
from .land_pattern import _json, _path

SCHEMA = 'agent-ee.component-polarity/1'
KEYS = frozenset({'schema', 'lcsc', 'mpn', 'manufacturer', 'supplier_capture_sha256', 'pin_polarity',
                  'manufacturer_source', 'package_feature_ids', 'symbol_feature_ids', 'rationale', 'limitation'})
SOURCE_KEYS = frozenset({'path', 'sha256', 'page', 'locator', 'statement'})
POLARITIES = ('anode', 'cathode')
MIN_RATIONALE = 80
SHA256 = re.compile(r'[0-9a-f]{64}')
# A native shape id is an identifier-like `~`/`^^` field ending in a digit (`gge1050`, owned `d1`).
FEATURE_ID = re.compile(r'[A-Za-z][A-Za-z0-9_]*[0-9]')


def _text(value, label, minimum=1):
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise ValueError(f'polarity evidence {label} must be text of at least {minimum} characters')
    return value


def feature_ids(shapes):
    """Every native EasyEDA shape id (`gge…`) named by a list of shape records."""
    found = set()
    for shape in shapes:
        if isinstance(shape, str):
            found.update(field for part in shape.split('^^') for field in part.split('~')
                         if FEATURE_ID.fullmatch(field))
    return found


def validate_record(document, suppliers, read):
    """The record, verified against the captured suppliers and inputs; ValueError on any defect.

    `suppliers` is `cpl_rotation.load_suppliers` output ({LCSC: raw bytes, sha256, ...});
    `read(path)` returns captured bytes after verifying their candidate digest.
    """
    if not isinstance(document, dict) or set(document) != KEYS:
        raise ValueError('polarity evidence fields must be exactly ' + ', '.join(sorted(KEYS)))
    if document['schema'] != SCHEMA:
        raise ValueError(f'polarity evidence schema must be {SCHEMA}')
    lcsc = _text(document['lcsc'], 'lcsc')
    _text(document['mpn'], 'mpn')
    _text(document['manufacturer'], 'manufacturer')
    supplier = suppliers.get(lcsc)
    if supplier is None:
        raise ValueError(f'polarity evidence names {lcsc}, which has no declared supplier capture')
    sha = document['supplier_capture_sha256']
    if not isinstance(sha, str) or not SHA256.fullmatch(sha) or sha != supplier['sha256']:
        raise ValueError(f'polarity evidence for {lcsc} is bound to another supplier capture digest')
    try:
        pins = {p['number'] for p in symbol_pins(supplier['raw'], lcsc)['pins']}
        raw = _json(supplier['raw'])
    except (SupplierError, ValueError) as exc:
        raise ValueError(f'polarity evidence for {lcsc}: supplier capture unreadable: {exc}') from exc
    mapping = document['pin_polarity']
    if (not isinstance(mapping, dict) or '1' not in mapping or not mapping
            or any(not isinstance(k, str) or v not in POLARITIES for k, v in mapping.items())):
        raise ValueError(f'polarity evidence for {lcsc} pin_polarity must map pin "1" (and others) to anode|cathode')
    unknown = sorted(set(mapping) - pins)
    if unknown:
        raise ValueError(f'polarity evidence for {lcsc} names symbol pins the capture lacks: {unknown}')
    if len(set(mapping.values())) < 2 and len(mapping) > 1:
        raise ValueError(f'polarity evidence for {lcsc} gives every pin the same polarity')
    source = document['manufacturer_source']
    if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
        raise ValueError('polarity evidence manufacturer_source must be exactly {path, sha256, page, locator, statement}')
    _path(source['path'], 'manufacturer source path')
    if not isinstance(source['sha256'], str) or not SHA256.fullmatch(source['sha256']):
        raise ValueError('polarity evidence manufacturer_source.sha256 must be a lowercase sha256')
    if type(source['page']) is not int or source['page'] < 1:
        raise ValueError('polarity evidence manufacturer_source.page must be a positive integer')
    _text(source['locator'], 'manufacturer_source.locator', 8)
    _text(source['statement'], 'manufacturer_source.statement', 4)
    from hashlib import sha256 as _sha
    data = read(source['path'])
    if _sha(data).hexdigest() != source['sha256']:
        raise ValueError(f'polarity evidence manufacturer source {source["path"]} differs from its declared sha256')
    shapes = raw.get('dataStr', {}).get('shape', []) if isinstance(raw, dict) else []
    package = raw.get('packageDetail', {}).get('dataStr', {}).get('shape', []) if isinstance(raw, dict) else []
    for key, available in (('package_feature_ids', feature_ids(package)), ('symbol_feature_ids', feature_ids(shapes))):
        ids = document[key]
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not FEATURE_ID.fullmatch(i) for i in ids):
            raise ValueError(f'polarity evidence {key} must list native shape ids')
        missing = sorted(set(ids) - available)
        if missing:
            raise ValueError(f'polarity evidence {key} names shapes absent from the {lcsc} capture: {missing}')
    _text(document['rationale'], 'rationale', MIN_RATIONALE)
    _text(document['limitation'], 'limitation', 8)
    return document


def load_evidence(read, paths, suppliers):
    """{LCSC: validated record with its captured path} for every declared evidence path."""
    evidence = {}
    for path in paths:
        record = validate_record(_json(read(path)), suppliers, read)
        if record['lcsc'] in evidence:
            raise ValueError(f'two polarity evidence records are declared for {record["lcsc"]}')
        evidence[record['lcsc']] = {**record, 'path': path}
    return evidence
