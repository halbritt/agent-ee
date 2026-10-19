"""Datasheet-derived footprints accepted only on dual independent extraction agreement.

Two isolated agent extractions, separate invocations through the reviewer seam
``reviewers.dispatch``, each read only one captured datasheet PDF. Each returns
a pad table, optionally a typical-application circuit, and page-cited facts in
the shared ``datasheet_facts`` record format that bind every pad dimension.

``accept`` replays the retained extractions without any model call:

1. a deterministic comparison (0.01 mm on centre, copper size and hole; exact
   kind, shape and pin name; exact circuit component multiset and net
   partition), with every difference in one diagnostic;
2. fact coverage: every pad coordinate and dimension equals a cited fact, and
   every cited fact is verified on its page by the pinned pdftotext in the
   networkless sandbox (shared ``datasheet_facts`` rules);
3. emission of ``.kicad_mod``/``.kicad_sym`` with a KiCad-convention courtyard,
   a ``kicad-cli`` load/re-save, strict re-extraction of the re-saved pads, and
   the shared footprint-assignment pad/pin parity check.

Library files are published (one rename of a complete staged directory) only
when every stage succeeds. Anything else publishes nothing.
"""
import argparse
from collections import Counter
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from fractions import Fraction
import hashlib
import json
import re
import shutil
import sys
import uuid
from pathlib import Path, PurePosixPath

from .candidate import safe_relative
from .datasheet_facts import (EXTRACTOR, page_support, split_pages, validate_extractor,
                              validate_fact_records, value_in_quote, verify_extractor)
from .storage import identity, json_bytes, read_json, rename_noreplace, write_json

REQUEST = 'agent-ee.footprint-extraction-request/1'
EXTRACTION = 'agent-ee.footprint-extraction/1'
RECORD = 'agent-ee.footprint-extraction-record/1'
AGREEMENT = 'agent-ee.footprint-agreement/1'
PAD_TABLE = 'agent-ee.pad-table/1'
VERSION = 'agent-ee.datasheet-footprint/1'
LABELS = ('extraction-a', 'extraction-b')
TOLERANCE = Decimal('0.01')
# KiCad library convention: 0.25 mm courtyard clearance on a 0.01 mm grid. Only
# pad copper is known here (no body outline), so the courtyard bounds the pads.
COURTYARD_CLEARANCE = Decimal('0.25')
COURTYARD_GRID = Decimal('0.01')
PAGE_DPI = 200
PDFTOPPM = '/usr/bin/pdftoppm'
SEARCH_LIMIT = 200000
KINDS = ('smd', 'thru_hole', 'np_thru_hole')
SHAPES = ('rect', 'circle', 'oval')
FIELDS = ('x', 'y', 'w', 'h', 'hole_w', 'hole_h')
DIMENSIONS = ('w', 'h', 'hole_w', 'hole_h')
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}\Z')
_DECIMAL = re.compile(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?\Z')
_PLAIN = re.compile(r'(0|[1-9][0-9]*)(\.[0-9]+)?\Z')
_SCALE = re.compile(r'-?[1-9][0-9]*(/[1-9][0-9]*)?\Z')
_HEX = re.compile(r'[0-9a-f]{64}\Z')

_TEXT = {'type': 'string', 'minLength': 1}
_NUMBER = {'type': 'string', 'pattern': '^' + _DECIMAL.pattern[:-2] + '$'}
_BINDING = {'anyOf': [{'type': 'null'}, {'type': 'object', 'additionalProperties': False,
    'required': ['fact', 'scale'], 'properties': {'fact': _TEXT,
        'scale': {'type': 'string', 'pattern': '^' + _SCALE.pattern[:-2] + '$'}}}]}
EXTRACTION_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['schema', 'part_number', 'pdf_sha256', 'pads', 'circuit', 'facts', 'limitations'],
    'properties': {
        'schema': {'type': 'string', 'enum': [EXTRACTION]}, 'part_number': _TEXT,
        'pdf_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
        'pads': {'type': 'array', 'minItems': 1, 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['number', 'name', 'kind', 'shape', 'x', 'y', 'w', 'h', 'hole', 'cite'],
            'properties': {'number': {'type': 'string'}, 'name': {'type': 'string'},
                'kind': {'type': 'string', 'enum': list(KINDS)}, 'shape': {'type': 'string', 'enum': list(SHAPES)},
                **{k: _NUMBER for k in ('x', 'y', 'w', 'h')},
                'hole': {'anyOf': [{'type': 'null'}, {'type': 'object', 'additionalProperties': False,
                    'required': ['shape', 'w', 'h'], 'properties': {
                        'shape': {'type': 'string', 'enum': ['circle', 'oval']}, 'w': _NUMBER, 'h': _NUMBER}}]},
                'cite': {'type': 'object', 'additionalProperties': False, 'required': list(FIELDS),
                         'properties': {k: _BINDING for k in FIELDS}}}}},
        'circuit': {'anyOf': [{'type': 'null'}, {'type': 'object', 'additionalProperties': False,
            'required': ['components', 'nets'], 'properties': {
                'components': {'type': 'array', 'minItems': 1, 'items': {'type': 'object',
                    'additionalProperties': False, 'required': ['ref', 'type', 'value'],
                    'properties': {'ref': _TEXT, 'type': _TEXT, 'value': _TEXT}}},
                'nets': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                    'required': ['name', 'pins'], 'properties': {'name': _TEXT,
                        'pins': {'type': 'array', 'minItems': 1, 'items': _TEXT}}}}}}]},
        'facts': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['id', 'part_number', 'kind', 'value', 'unit', 'conditions', 'source'],
            'properties': {'id': _TEXT, 'part_number': _TEXT, 'kind': {'type': 'string', 'enum': ['dimension']},
                'value': _TEXT, 'unit': {'type': 'string', 'enum': ['mm']}, 'conditions': _TEXT,
                'source': {'type': 'object', 'additionalProperties': False,
                    'required': ['path', 'sha256', 'page', 'locator', 'quote'], 'properties': {
                        'path': _TEXT, 'sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                        'page': {'type': 'integer', 'minimum': 1}, 'locator': _TEXT, 'quote': _TEXT}}}}},
        'limitations': {'type': 'array', 'items': {'type': 'string'}}}}


class ExtractionError(ValueError):
    """A request, retained record or extraction value that violates its contract."""


def _exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ExtractionError(f'{label} requires exactly {sorted(keys)}')
    return value


def _text(value, label, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or value != value.strip():
        raise ExtractionError(f'{label} must be trimmed text')
    return value


def _decimal(value, label, positive=False):
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise ExtractionError(f'{label} must be decimal millimetre text')
    number = Decimal(value)
    if positive and number <= 0:
        raise ExtractionError(f'{label} must be positive')
    return number


def _fmt(value):
    text = format(Decimal(value).normalize(), 'f')
    return '0' if text in ('-0', '0') else text


def _quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def validate_definition(label, definition):
    """The shared profile validator owns harness/model/effort/timeout rules; extraction needs file tools."""
    from .profiles import validate_profile
    if not isinstance(definition, dict) or set(definition) - {'harness', 'model', 'effort', 'timeout_s', 'tool_mode'}:
        raise ExtractionError(f'{label} definition has unknown fields')
    if definition.get('tool_mode', 'investigate') != 'investigate':
        raise ExtractionError(f'{label} must read the PDF: tool_mode investigate')
    reviewer = {**definition, 'role': label}
    try:
        validate_profile({'schema': 'agent-ee.profile/1', 'name': 'datasheet-extraction',
                          'reviewers': [reviewer, {**reviewer, 'role': label + '-check'}], 'adjudicator': definition})
    except (ValueError, AttributeError) as exc:
        raise ExtractionError(f'{label}: {exc}') from exc


def validate_request(request):
    _exact(request, ('schema', 'part_number', 'library', 'footprint', 'pdf', 'extractor', 'kicad_version',
                     'extractors'), 'request')
    if request['schema'] != REQUEST:
        raise ExtractionError('unsupported extraction request schema')
    _text(request['part_number'], 'part_number')
    for key in ('library', 'footprint'):
        if not isinstance(request[key], str) or not _NAME.fullmatch(request[key]):
            raise ExtractionError(f'{key} must be a plain KiCad name')
    pdf = _exact(request['pdf'], ('path', 'sha256'), 'pdf')
    try:
        if str(safe_relative(pdf['path'])) != pdf['path'] or not pdf['path'].endswith('.pdf'):
            raise ValueError('pdf path must be a normal relative .pdf path')
        validate_extractor(request['extractor'])
    except ValueError as exc:
        raise ExtractionError(str(exc)) from exc
    if not isinstance(pdf['sha256'], str) or not _HEX.fullmatch(pdf['sha256']):
        raise ExtractionError('pdf sha256 required')
    _text(request['kicad_version'], 'kicad_version')
    extractors = request['extractors']
    if not isinstance(extractors, dict) or set(extractors) != set(LABELS):
        raise ExtractionError('exactly two extractors, ' + ' and '.join(LABELS), )
    for label in LABELS:
        validate_definition(label, extractors[label])
    return request


def _binding(value, label):
    if value is None:
        return None
    _exact(value, ('fact', 'scale'), label)
    _text(value['fact'], label + ' fact')
    if not isinstance(value['scale'], str) or not _SCALE.fullmatch(value['scale']):
        raise ExtractionError(label + ' scale must be a nonzero integer or n/d')
    return dict(fact=value['fact'], scale=value['scale'])


def _pin(text, refs, label):
    ref, dot, pin = text.partition('.')
    if not dot or ref not in refs or not pin:
        raise ExtractionError(f'{label}: pin {text!r} must be REF.PIN of a listed component')
    return ref, pin


def _circuit(value, numbers, label):
    if value is None:
        return None
    _exact(value, ('components', 'nets'), label)
    if not isinstance(value['components'], list) or not value['components'] or not isinstance(value['nets'], list):
        raise ExtractionError(label + ' needs components and nets')
    components = {}
    for item in value['components']:
        _exact(item, ('ref', 'type', 'value'), label + ' component')
        ref = _text(item['ref'], label + ' ref')
        if ref in components or '.' in ref:
            raise ExtractionError(f'{label}: duplicate or dotted ref {ref!r}')
        components[ref] = (_text(item['type'], label + ' type'), _text(item['value'], label + ' value'))
    devices = [r for r, (kind, _) in components.items() if kind == 'device']
    if len(devices) != 1:
        raise ExtractionError(label + ' needs exactly one component of type device')
    nets, seen = [], set()
    for net in value['nets']:
        _exact(net, ('name', 'pins'), label + ' net')
        _text(net['name'], label + ' net name')
        if not isinstance(net['pins'], list) or not net['pins']:
            raise ExtractionError(label + ' net needs pins')
        pins = [_pin(p, components, label) for p in net['pins']]
        for ref, pin in pins:
            if (ref, pin) in seen:
                raise ExtractionError(f'{label}: pin {ref}.{pin} appears in more than one net')
            if ref == devices[0] and pin not in numbers:
                raise ExtractionError(f'{label}: device pin {pin!r} is not an electrical pad number')
            seen.add((ref, pin))
        nets.append(frozenset(pins))
    return dict(components=components, nets=nets, device=devices[0], raw=value)


def validate_extraction(value, request):
    """Normalized extraction (Decimal millimetres) or ExtractionError; coverage is separate."""
    _exact(value, EXTRACTION_SCHEMA['required'], 'extraction')
    if value['schema'] != EXTRACTION:
        raise ExtractionError('unsupported extraction schema')
    if value['part_number'] != request['part_number'] or value['pdf_sha256'] != request['pdf']['sha256']:
        raise ExtractionError('extraction names a different part or PDF')
    facts = value['facts']
    if not isinstance(facts, list):
        raise ExtractionError('facts must be a list')
    try:
        validate_fact_records(facts)
    except ValueError as exc:
        raise ExtractionError(str(exc)) from exc
    for fact in facts:
        _exact(fact, EXTRACTION_SCHEMA['properties']['facts']['items']['required'], 'fact')
    if not isinstance(value['limitations'], list) or any(not isinstance(x, str) for x in value['limitations']):
        raise ExtractionError('limitations must be text')
    if not isinstance(value['pads'], list) or not value['pads']:
        raise ExtractionError('nonempty pad list required')
    pads, names = [], {}
    for index, raw in enumerate(value['pads']):
        label = f'pad[{index}]'
        _exact(raw, EXTRACTION_SCHEMA['properties']['pads']['items']['required'], label)
        number, name = _text(raw['number'], label + ' number', True), _text(raw['name'], label + ' name', True)
        kind, shape = raw['kind'], raw['shape']
        if kind not in KINDS or shape not in SHAPES:
            raise ExtractionError(label + ': unsupported kind or shape')
        if (kind == 'np_thru_hole') != (number == '') or (number == '') != (name == ''):
            raise ExtractionError(label + ': only an unnamed np_thru_hole pad is unnumbered')
        if number and names.setdefault(number, name) != name:
            raise ExtractionError(f'{label}: pads numbered {number!r} carry different names')
        pad = dict(index=index, number=number, name=name, kind=kind, shape=shape,
                   x=_decimal(raw['x'], label + ' x'), y=_decimal(raw['y'], label + ' y'),
                   w=_decimal(raw['w'], label + ' w', True), h=_decimal(raw['h'], label + ' h', True),
                   hole=None, hole_w=None, hole_h=None)
        if shape == 'circle' and pad['w'] != pad['h']:
            raise ExtractionError(label + ': circle with unequal size')
        hole = raw['hole']
        if (kind == 'smd') != (hole is None):
            raise ExtractionError(label + ': drilled kinds need a hole and SMD pads none')
        if hole is not None:
            _exact(hole, ('shape', 'w', 'h'), label + ' hole')
            if hole['shape'] not in ('circle', 'oval'):
                raise ExtractionError(label + ': hole shape must be circle or oval')
            pad.update(hole=hole['shape'], hole_w=_decimal(hole['w'], label + ' hole w', True),
                       hole_h=_decimal(hole['h'], label + ' hole h', True))
            if hole['shape'] == 'circle' and pad['hole_w'] != pad['hole_h']:
                raise ExtractionError(label + ': circular hole with unequal size')
            if kind == 'thru_hole' and not (pad['hole_w'] < pad['w'] and pad['hole_h'] < pad['h']):
                raise ExtractionError(label + ': plated hole needs a copper annulus')
            if kind == 'np_thru_hole' and ((pad['w'], pad['h']) != (pad['hole_w'], pad['hole_h'])
                                           or (shape == 'rect') or (shape == 'circle') != (hole['shape'] == 'circle')):
                raise ExtractionError(label + ': np_thru_hole copper must equal its hole')
        cite = _exact(raw['cite'], FIELDS, label + ' cite')
        pad['cite'] = {k: _binding(cite[k], f'{label} cite {k}') for k in FIELDS}
        if hole is None and (pad['cite']['hole_w'] or pad['cite']['hole_h']):
            raise ExtractionError(label + ': hole citations on an undrilled pad')
        pads.append(pad)
    numbers = {p['number'] for p in pads if p['number']}
    return dict(pads=pads, facts={f['id']: f for f in facts}, circuit=_circuit(value['circuit'], numbers, 'circuit'))


def _pairs(a_pads, b_pads):
    """Pads of one number, paired by unique centre agreement, else by sorted position."""
    near = {i: [j for j, q in enumerate(b_pads) if abs(p['x'] - q['x']) <= TOLERANCE and abs(p['y'] - q['y']) <= TOLERANCE]
            for i, p in enumerate(a_pads)}
    chosen = [m[0] for m in near.values() if len(m) == 1]
    if len(a_pads) == len(b_pads) and all(len(m) == 1 for m in near.values()) and len(set(chosen)) == len(chosen):
        return [(a_pads[i], b_pads[near[i][0]]) for i in near]
    order = lambda p: (p['x'], p['y'], p['index'])
    return list(zip(sorted(a_pads, key=order), sorted(b_pads, key=order)))


def _differences(a, b):
    found = []
    groups = {}
    for side, extraction in (('a', a), ('b', b)):
        for pad in extraction['pads']:
            groups.setdefault(pad['number'], {'a': [], 'b': []})[side].append(pad)
    for number in sorted(groups):
        group = groups[number]
        if len(group['a']) != len(group['b']):
            found.append(dict(code='pad_count', number=number, a=len(group['a']), b=len(group['b'])))
            continue
        for p, q in _pairs(group['a'], group['b']):
            where = dict(number=number, a_index=p['index'], b_index=q['index'])
            for key in ('kind', 'shape', 'name', 'hole'):
                if p[key] != q[key]:
                    found.append(dict(where, code='mismatch', field=key, a=p[key], b=q[key]))
            for key in FIELDS:
                if p[key] is None or q[key] is None:
                    continue
                delta = abs(p[key] - q[key])
                if delta > TOLERANCE:
                    found.append(dict(where, code='out_of_tolerance', field=key, a=_fmt(p[key]), b=_fmt(q[key]),
                                      delta_mm=_fmt(delta)))
    return found


def _match_circuit(a, b):
    """Exact component multiset and net partition up to a type/value-preserving ref bijection."""
    if a is None and b is None:
        return [], None
    if (a is None) != (b is None):
        return [dict(code='circuit_presence', a=a is not None, b=b is not None)], None
    ka, kb = Counter(a['components'].values()), Counter(b['components'].values())
    if ka != kb:
        return [dict(code='circuit_components', only_a=sorted('/'.join(k) for k in (ka - kb).elements()),
                     only_b=sorted('/'.join(k) for k in (kb - ka).elements()))], None
    if sorted(map(len, a['nets'])) != sorted(map(len, b['nets'])):
        return [dict(code='circuit_nets', reason='net sizes differ')], None
    target = set(b['nets'])
    classes = Counter(a['components'].values())
    order = sorted(a['components'], key=lambda r: (classes[a['components'][r]], r))
    position = {r: i for i, r in enumerate(order)}
    closing = {r: [] for r in order}
    for net in a['nets']:
        closing[max((ref for ref, _ in net), key=position.get)].append(net)
    mapping, used, steps = {}, set(), [0]

    def search(depth):
        if depth == len(order):
            return True
        ref = order[depth]
        for candidate in sorted(b['components']):
            if candidate in used or b['components'][candidate] != a['components'][ref]:
                continue
            steps[0] += 1
            if steps[0] > SEARCH_LIMIT:
                raise OverflowError
            mapping[ref] = candidate
            used.add(candidate)
            if all(frozenset((mapping[r], pin) for r, pin in net) in target for net in closing[ref]) and search(depth + 1):
                return True
            used.discard(candidate)
            del mapping[ref]
        return False

    try:
        if search(0):
            return [], dict(sorted(mapping.items()))
    except OverflowError:
        return [dict(code='circuit_search_limit', limit=SEARCH_LIMIT)], None
    return [dict(code='circuit_nets', reason='no type/value-preserving reference mapping gives the same net partition')], None


def compare(a, b):
    """All differences between two normalized extractions; empty means agreement."""
    differences = _differences(a, b)
    circuit, mapping = _match_circuit(a['circuit'], b['circuit'])
    return differences + circuit, mapping


def _coordinate_scale_supported(pad, key, fact, scale, pads):
    """A quoted pitch declares a centered row; a quoted span declares its halves."""
    declaration = re.fullmatch(
        r'(?P<role>(?:(?:pad|pin|lead)[ \t]+)?pitch|'
        r'(?:row|column)[ \t]+(?:spacing|span)|(?:pad|pin|lead)[ \t]+span)'
        r'[ \t]+(?:[A-Za-z][A-Za-z0-9_]*[ \t]+)?' + re.escape(fact['value']) + r'(?:[ \t]+mm)?',
        fact['source']['quote'], re.IGNORECASE)
    if declaration is None or Decimal(fact['value']) <= 0:
        return False
    role = declaration['role'].lower().split()
    if role[-1] != 'pitch':
        if (role[0] == 'row' and key != 'y') or (role[0] == 'column' and key != 'x'):
            return False
        return abs(scale) == Fraction(1, 2)
    other = 'y' if key == 'x' else 'x'
    positions = sorted({Fraction(p[key]) for p in pads
                        if p[other] == pad[other] and p['kind'] == pad['kind']})
    # Indices are geometric sites, not pin numbers or extraction-list order.
    # Every site must fit the same centered pitch, so sparse/arbitrary integers
    # cannot justify an otherwise unsupported coordinate.
    pitch = Fraction(Decimal(fact['value']))
    return len(positions) > 1 and all(
        position == pitch * Fraction(2 * i - len(positions) + 1, 2)
        for i, position in enumerate(positions)) and scale == Fraction(
            2 * positions.index(Fraction(pad[key])) - len(positions) + 1, 2)


def _coverage(label, extraction, request, verified):
    gaps, cited = [], set()
    for pad in extraction['pads']:
        for key in FIELDS:
            value, binding = pad[key], pad['cite'][key]
            if value is None:
                continue
            where = dict(extraction=label, number=pad['number'], index=pad['index'], field=key)
            if binding is None:
                # The declared footprint origin needs no fact; any other value does.
                if not (key in ('x', 'y') and value == 0):
                    gaps.append(dict(where, reason='missing_fact'))
                continue
            fact = extraction['facts'].get(binding['fact'])
            cited.add(binding['fact'])
            scale = Fraction(binding['scale'])
            reason = None
            if fact is None:
                reason = 'unknown_fact'
            elif fact['unit'] != 'mm' or not _PLAIN.fullmatch(fact['value']):
                reason = 'fact_not_plain_millimetres'
            elif fact['part_number'] != request['part_number'] or fact['source']['path'] != request['pdf']['path'] \
                    or fact['source']['sha256'] != request['pdf']['sha256']:
                reason = 'fact_names_another_part_or_pdf'
            elif key in DIMENSIONS and scale != 1:
                reason = 'dimension_must_equal_fact'
            elif Fraction(Decimal(fact['value'])) * scale != Fraction(value):
                reason = 'value_differs_from_fact'
            elif verified.get((label, binding['fact']), {}).get('status') != 'verified':
                reason = 'fact_unverified'
            elif key in ('x', 'y') and not _coordinate_scale_supported(pad, key, fact, scale, extraction['pads']):
                reason = 'unsupported_coordinate_scale'
            if reason:
                gaps.append(dict(where, fact=binding['fact'], reason=reason))
    return gaps, cited


def _run(inputs, work, argv, timeout=120):
    from .executor import execute
    record = execute(inputs, work, argv, timeout=timeout)
    return record, Path(work)


def _first_line(work):
    lines = (work / 'console.log').read_text(errors='replace').splitlines()
    return lines[0] if lines else ''


def verify_facts(request, extractions, inputs, work):
    """pdftotext verification of every cited fact (shared token/page rules); raises on tool failure."""
    tool = request['extractor']
    verify_extractor(tool)
    record, version = _run(inputs, work / 'pdftotext-version', [EXTRACTOR, '-v'])
    if record['execution'] != 'ok' or _first_line(version) != tool['version']:
        raise ExtractionError('installed pdftotext version unavailable or differs from the declaration')
    record, text = _run(inputs, work / 'pdftotext', [EXTRACTOR, '-layout', '/candidate/' + request['pdf']['path'],
                                                     '/work/document.txt'])
    if record['execution'] != 'ok' or not (text / 'document.txt').is_file():
        raise ExtractionError('pdftotext ' + record['execution'])
    pages = split_pages((text / 'document.txt').read_text(encoding='utf-8'))
    verify_extractor(tool)
    results = {}
    for label, extraction, cited in extractions:
        for fact_id in sorted(cited):
            fact = extraction['facts'].get(fact_id)
            if fact is None:
                continue
            if not value_in_quote(fact['value'], fact['unit'], fact['source']['quote']):
                status, reason = 'rejected', 'value_absent_from_quote'
            elif fact['source']['sha256'] != request['pdf']['sha256'] or fact['source']['path'] != request['pdf']['path']:
                status, reason = 'rejected', 'pdf_hash_mismatch'
            else:
                status, reason = page_support(fact['source'], pages)
            results[(label, fact_id)] = dict(extraction=label, id=fact_id, status=status, reason=reason)
    return results


def render_pages(request, pages, inputs, work):
    """Server-rendered cited pages at 200 DPI, bound to the PDF hash by the request."""
    record, version = _run(inputs, work / 'pdftoppm-version', [PDFTOPPM, '-v'])
    if record['execution'] != 'ok':
        raise ExtractionError('pdftoppm unavailable')
    tool = dict(path=PDFTOPPM, sha256=hashlib.sha256(Path(PDFTOPPM).read_bytes()).hexdigest(),
                version=_first_line(version), dpi=PAGE_DPI)
    rendered = []
    for page in sorted(pages):
        record, out = _run(inputs, work / f'page-{page}', [PDFTOPPM, '-r', str(PAGE_DPI), '-f', str(page), '-l',
                                                         str(page), '-png', '-singlefile',
                                                         '/candidate/' + request['pdf']['path'], '/work/page'])
        image = out / 'page.png'
        if record['execution'] != 'ok' or not image.is_file():
            raise ExtractionError(f'pdftoppm page {page} {record["execution"]}')
        rendered.append(dict(page=page, path=f'pages/page-{page}.png',
                             sha256=hashlib.sha256(image.read_bytes()).hexdigest(), source=image))
    return tool, rendered


def _courtyard(pads):
    left = min(p['x'] - p['w'] / 2 for p in pads) - COURTYARD_CLEARANCE
    right = max(p['x'] + p['w'] / 2 for p in pads) + COURTYARD_CLEARANCE
    top = min(p['y'] - p['h'] / 2 for p in pads) - COURTYARD_CLEARANCE
    bottom = max(p['y'] + p['h'] / 2 for p in pads) + COURTYARD_CLEARANCE
    floor = lambda v: v.quantize(COURTYARD_GRID, rounding=ROUND_FLOOR)
    ceiling = lambda v: v.quantize(COURTYARD_GRID, rounding=ROUND_CEILING)
    return floor(left), floor(top), ceiling(right), ceiling(bottom)


def _technology(pads):
    kinds = {p['kind'] for p in pads if p['number']}
    return {frozenset({'smd'}): 'smd', frozenset({'thru_hole'}): 'through_hole'}.get(frozenset(kinds), 'mixed')


def _uuid(request, item):
    """Deterministic item identity so a kicad-cli re-save is reproducible."""
    return _quote(str(uuid.uuid5(uuid.NAMESPACE_URL, f'agent-ee:{request["pdf"]["sha256"]}:{request["footprint"]}:{item}')))


def emit_footprint(request, pads):
    left, top, right, bottom = _courtyard(pads)
    font = '(effects (font (size 1 1) (thickness 0.15)))'
    lines = [f'(footprint {_quote(request["footprint"])}', '\t(version 20260206)', '\t(generator "agent_ee")',
             f'\t(generator_version {_quote(VERSION)})', '\t(layer "F.Cu")',
             f'\t(descr {_quote(request["part_number"] + " land pattern by dual datasheet extraction agreement, PDF sha256 " + request["pdf"]["sha256"])})',
             f'\t(property "Reference" "REF**" (at 0 {_fmt(top - 1)} 0) (layer "F.SilkS") (uuid {_uuid(request, "reference")}) {font})',
             f'\t(property "Value" {_quote(request["footprint"])} (at 0 {_fmt(bottom + 1)} 0) (layer "F.Fab") '
             f'(uuid {_uuid(request, "value")}) {font})',
             *(f'\t(property {_quote(key)} "" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid {_uuid(request, key)}) '
               '(effects (font (size 1.27 1.27))))' for key in ('Datasheet', 'Description')),
             '\t(attr {})'.format('smd' if _technology(pads) == 'smd' else 'through_hole'),
             f'\t(fp_rect (start {_fmt(left)} {_fmt(top)}) (end {_fmt(right)} {_fmt(bottom)}) '
             f'(stroke (width 0.05) (type default)) (fill no) (layer "F.CrtYd") (uuid {_uuid(request, "courtyard")}))']
    layers = {'smd': '"F.Cu" "F.Paste" "F.Mask"', 'thru_hole': '"*.Cu" "*.Mask"', 'np_thru_hole': '"*.Cu" "*.Mask"'}
    for index, pad in enumerate(pads):
        drill = ''
        if pad['hole'] == 'circle':
            drill = f' (drill {_fmt(pad["hole_w"])})'
        elif pad['hole'] == 'oval':
            drill = f' (drill oval {_fmt(pad["hole_w"])} {_fmt(pad["hole_h"])})'
        lines.append(f'\t(pad {_quote(pad["number"])} {pad["kind"]} {pad["shape"]} (at {_fmt(pad["x"])} {_fmt(pad["y"])}) '
                     f'(size {_fmt(pad["w"])} {_fmt(pad["h"])}){drill} (layers {layers[pad["kind"]]}) '
                     f'(uuid {_uuid(request, f"pad-{index}")}))')
    return ('\n'.join(lines) + '\n)\n').encode()


def _natural(number):
    return [(0, int(part), '') if part.isdigit() else (1, 0, part) for part in re.findall(r'\d+|\D+', number)]


def emit_symbol(request, pads):
    pins = sorted({p['number']: p['name'] for p in pads if p['number']}.items(), key=lambda item: _natural(item[0]))
    name, font = request['footprint'], '(effects (font (size 1.27 1.27)))'
    top = Decimal('1.27') * (len(pins) - 1) + Decimal('2.54')
    lines = ['(kicad_symbol_lib', '\t(version 20251024)', '\t(generator "agent_ee")',
             f'\t(generator_version {_quote(VERSION)})', f'\t(symbol {_quote(name)}',
             '\t\t(exclude_from_sim no)', '\t\t(in_bom yes)', '\t\t(on_board yes)',
             f'\t\t(property "Reference" "U" (at 0 {_fmt(top + Decimal("1.27"))} 0) {font})',
             f'\t\t(property "Value" {_quote(request["part_number"])} (at 0 {_fmt(-top - Decimal("1.27"))} 0) {font})',
             f'\t\t(property "Footprint" {_quote(request["library"] + ":" + name)} (at 0 0 0) '
             '(effects (font (size 1.27 1.27)) (hide yes)))',
             '\t\t(property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))',
             f'\t\t(symbol {_quote(name + "_0_1")} (rectangle (start -7.62 {_fmt(top)}) (end 7.62 {_fmt(-top)}) '
             '(stroke (width 0.254) (type default)) (fill (type background))))',
             f'\t\t(symbol {_quote(name + "_1_1")}']
    for index, (number, pin_name) in enumerate(pins):
        y = top - Decimal('2.54') - Decimal('2.54') * index
        # Electrical types are not datasheet-derived here; unspecified keeps ERC honest.
        lines.append(f'\t\t\t(pin unspecified line (at -10.16 {_fmt(y)} 0) (length 2.54) '
                     f'(name {_quote(pin_name)} {font}) (number {_quote(number)} {font}))')
    lines += ['\t\t)', '\t)', ')']
    return ('\n'.join(lines) + '\n').encode()


def _expected_pattern(pads):
    mm = Fraction(1000000)
    text = lambda v: str(Fraction(v) * mm)
    found = []
    for pad in pads:
        if pad['kind'] == 'np_thru_hole':
            continue
        found.append((pad['number'], pad['shape'], text(pad['x']), text(pad['y']), text(pad['w']), text(pad['h'])))
    return sorted(found)


def _pattern(extracted):
    return sorted((p['number'], p['primitive']['shape'], *p['primitive']['center'], *p['primitive']['size'])
                  for p in extracted['pattern']['pads'])


def _holes(extracted):
    return sorted((h['shape'], *h['center'], *h['size']) for h in extracted['pattern']['holes'])


def _expected_holes(pads):
    mm = Fraction(1000000)
    text = lambda v: str(Fraction(v) * mm)
    return sorted(('circle' if p['hole_w'] == p['hole_h'] else 'oval', text(p['x']), text(p['y']),
                   text(p['hole_w']), text(p['hole_h'])) for p in pads if p['hole'])


def _manifest(request, footprint):
    return dict(schema='agent-ee.footprint-libraries/1', complete=True, libraries=[dict(
        name=request['library'], members=[dict(path=f'{request["library"]}.pretty/{request["footprint"]}.kicad_mod',
                                               sha256=hashlib.sha256(footprint).hexdigest())])])


def load_and_parity(request, pads, footprint, symbol, staging, work):
    """kicad-cli load/re-save, strict re-extraction and the shared pad/pin assignment parity."""
    from .footprint_library import check_assignments
    from .land_pattern import PatternError, library_footprint
    library, name = request['library'], request['footprint']
    lib_id = f'{library}:{name}'
    record, version = _run(staging, work / 'kicad-version', ['kicad-cli', 'version'])
    if record['execution'] != 'ok' or _first_line(version) != request['kicad_version']:
        raise ExtractionError('kicad-cli unavailable or not the declared version')
    record, fp_out = _run(staging, work / 'fp-upgrade', ['kicad-cli', 'fp', 'upgrade', '--force', '--output',
                                                        '/work/resaved.pretty', f'/candidate/{library}.pretty'])
    resaved = fp_out / 'resaved.pretty' / f'{name}.kicad_mod'
    if record['execution'] != 'ok' or not resaved.is_file() or resaved.is_symlink():
        raise ExtractionError('kicad-cli did not load and re-save the generated footprint: ' + record['execution'])
    record, sym_out = _run(staging, work / 'sym-upgrade', ['kicad-cli', 'sym', 'upgrade', '--force', '--output',
                                                          '/work/resaved.kicad_sym', f'/candidate/{library}.kicad_sym'])
    resaved_symbol = sym_out / 'resaved.kicad_sym'
    if record['execution'] != 'ok' or not resaved_symbol.is_file() or resaved_symbol.is_symlink():
        raise ExtractionError('kicad-cli did not load and re-save the generated symbol: ' + record['execution'])
    member = f'{library}.pretty/{name}.kicad_mod'
    patterns = {}
    for kind, data in (('generated', footprint), ('kicad_resaved', resaved.read_bytes())):
        manifest = _manifest(request, data)
        try:
            patterns[kind] = library_footprint(manifest, {member: data}, lib_id)
        except PatternError as exc:
            raise ExtractionError(f'{kind} footprint is not qualified: {exc}') from exc
        if _pattern(patterns[kind]) != _expected_pattern(pads) or _holes(patterns[kind]) != _expected_holes(pads):
            raise ExtractionError(f'{kind} footprint pads differ from the agreed pad table')
    symbol_path = f'{library}.kicad_sym'
    captures = {member: footprint, symbol_path: symbol}
    declaration = dict(schema='agent-ee.footprint-assignments-input/1',
                       libraries=[dict(name=library, path=symbol_path, sha256=hashlib.sha256(symbol).hexdigest())],
                       components=[dict(reference='U1', lib_id=lib_id, footprint=lib_id,
                                        assembly=_technology(pads), connector=None)])
    assignment = check_assignments(_manifest(request, footprint), captures, declaration)
    kicad = dict(version=_first_line(version), footprint_resaved_sha256=hashlib.sha256(resaved.read_bytes()).hexdigest(),
                 symbol_resaved_sha256=hashlib.sha256(resaved_symbol.read_bytes()).hexdigest(),
                 reextracted_pads=patterns['kicad_resaved']['inventory'])
    return kicad, assignment


def _load_record(run, label, request, request_sha):
    path = run / 'extractions' / f'{label}.json'
    if not path.is_file() or path.is_symlink():
        return None, f'{label} retained extraction is missing'
    record = read_json(path)
    _exact(record, ('schema', 'label', 'request_sha256', 'definition', 'call', 'value'), label)
    if record['schema'] != RECORD or record['label'] != label or record['request_sha256'] != request_sha \
            or record['definition'] != request['extractors'][label]:
        raise ExtractionError(f'{label} retained record does not belong to this request and role')
    call = record['call']
    if not isinstance(call, dict) or call.get('execution') != 'ok' or record['value'] is None:
        execution = call.get('execution') if isinstance(call, dict) else None
        return None, f'{label} agent execution {execution or "missing"}; no fallback'
    return record, None


def _pad_record(pad, b_pad):
    hole = None if pad['hole'] is None else dict(shape=pad['hole'], w=_fmt(pad['hole_w']), h=_fmt(pad['hole_h']))
    return dict(number=pad['number'], name=pad['name'], kind=pad['kind'], shape=pad['shape'], x=_fmt(pad['x']),
                y=_fmt(pad['y']), w=_fmt(pad['w']), h=_fmt(pad['h']), hole=hole,
                cite={'extraction-a': pad['cite'], 'extraction-b': b_pad['cite']})


def accept(run_dir, out_dir):
    """Replay retained extractions into a new directory without model calls; return the agreement record."""
    run, out = Path(run_dir), Path(out_dir)
    if out.exists() or out.is_symlink():
        raise ExtractionError('acceptance output must be a new directory')
    out.mkdir(mode=0o700, parents=True)
    report = dict(schema=AGREEMENT, version=VERSION, tolerance_mm=_fmt(TOLERANCE), execution='failed',
                  decision='unknown', published=False, reason='', request_sha256=None, extractions={},
                  differences=[], coverage_gaps=[], facts=[], pages=None, circuit=None, kicad=None,
                  assignment=None, library=None,
                  limitations=['Dimensions are taken as extracted and cited; no IPC-7351 calculation.',
                               'Citation verification proves text occurrence, not row selection or applicability.',
                               'The reference circuit is agreed, not electrically proven.',
                               'Courtyard bounds pad copper only; no body outline is modelled.',
                               'Symbol pin electrical types are unspecified.'])

    def finish(execution, decision, reason):
        report.update(execution=execution, decision=decision, reason=reason)
        write_json(out / 'agreement.json', report)
        return report

    try:
        request = validate_request(read_json(run / 'request.json'))
        report['request_sha256'] = request_sha = identity(request)
        inputs = run / 'inputs'
        pdf = inputs / request['pdf']['path']
        if not pdf.is_file() or pdf.is_symlink() or hashlib.sha256(pdf.read_bytes()).hexdigest() != request['pdf']['sha256']:
            return finish('failed', 'unknown', 'captured PDF missing or hash mismatch')
        report['pdf'] = request['pdf']
        records, failures = {}, []
        for label in LABELS:
            record, failure = _load_record(run, label, request, request_sha)
            report['extractions'][label] = dict(execution=record['call']['execution'] if record else 'failed',
                                                reason=failure)
            if failure:
                failures.append(failure)
            records[label] = record
        if failures:
            return finish('failed', 'unknown', '; '.join(failures) + '; a single extraction is never accepted')
        extractions = {}
        for label in LABELS:
            try:
                extractions[label] = validate_extraction(records[label]['value'], request)
            except ExtractionError as exc:
                report['extractions'][label].update(execution='malformed', reason=str(exc))
        if len(extractions) != 2:
            return finish('malformed', 'unknown', 'extraction output violates its contract')
        a, b = (extractions[label] for label in LABELS)
        differences, mapping = compare(a, b)
        report['differences'] = differences
        if differences:
            return finish('ok', 'rejected', f'{len(differences)} difference(s) between independent extractions')
        work = out / 'work'
        cited = {label: _coverage(label, extractions[label], request, {})[1] for label in LABELS}
        try:
            verified = verify_facts(request, [(l, extractions[l], cited[l]) for l in LABELS], inputs, work)
        except (ExtractionError, ValueError, OSError) as exc:
            return finish('failed', 'unknown', 'fact verification unavailable: ' + str(exc))
        report['facts'] = [verified[key] for key in sorted(verified)]
        gaps = []
        for label in LABELS:
            gaps += _coverage(label, extractions[label], request, verified)[0]
        report['coverage_gaps'] = gaps
        pages = {extractions[l]['facts'][f]['source']['page'] for l in LABELS for f in cited[l] if f in extractions[l]['facts']}
        try:
            tool, rendered = render_pages(request, pages, inputs, work)
        except (ExtractionError, OSError) as exc:
            return finish('failed', 'unknown', 'page rendering unavailable: ' + str(exc))
        (out / 'pages').mkdir()
        for page in rendered:
            shutil.copyfile(page.pop('source'), out / page['path'])
        report['pages'] = dict(renderer=tool, pages=rendered)
        if gaps:
            return finish('unsupported', 'unknown', f'{len(gaps)} pad value(s) lack a verified datasheet fact')
        pairs = {p['index']: q for number in {p['number'] for p in a['pads']}
                 for p, q in _pairs([x for x in a['pads'] if x['number'] == number],
                                    [x for x in b['pads'] if x['number'] == number])}
        if a['circuit'] is not None:
            report['circuit'] = dict(agreed=a['circuit']['raw'], reference_mapping=mapping)
        footprint, symbol = emit_footprint(request, a['pads']), emit_symbol(request, a['pads'])
        staging = out / '.library-staging'
        library, name = request['library'], request['footprint']
        (staging / f'{library}.pretty').mkdir(parents=True)
        (staging / f'{library}.pretty' / f'{name}.kicad_mod').write_bytes(footprint)
        (staging / f'{library}.kicad_sym').write_bytes(symbol)
        try:
            report['kicad'], assignment = load_and_parity(request, a['pads'], footprint, symbol, staging, work)
        except (ExtractionError, OSError) as exc:
            shutil.rmtree(staging)
            return finish('failed', 'unknown', str(exc))
        report['assignment'] = dict(execution=assignment['execution'], outcome=assignment['outcome'],
                                    coverage=assignment['coverage'], findings=assignment['findings'],
                                    pin_numbers=[x.get('pin_numbers') for x in assignment['assignments']],
                                    canonical_sha256=assignment['canonical_sha256'])
        if assignment['execution'] != 'ok' or assignment['outcome'] != 'pass':
            shutil.rmtree(staging)
            if assignment['execution'] == 'ok':
                return finish('ok', 'rejected', 'symbol pin numbers differ from footprint pad numbers')
            return finish(assignment['execution'], 'unknown', 'footprint assignment parity unavailable')
        facts = [dict(fact, id=f'{label}:{fact["id"]}') for label in LABELS
                 for fact_id, fact in sorted(extractions[label]['facts'].items()) if fact_id in cited[label]]
        published = {
            'footprint-libraries.json': json_bytes(_manifest(request, footprint)),
            'facts.json': json_bytes(dict(schema='agent-ee.datasheet-facts/1', facts=facts)),
            'pad-table.json': json_bytes(dict(schema=PAD_TABLE, part_number=request['part_number'],
                                              pdf=request['pdf'], published_values='extraction-a',
                                              pads=[_pad_record(p, pairs[p['index']]) for p in a['pads']])),
        }
        if report['circuit'] is not None:
            published['reference-circuit.json'] = json_bytes(dict(schema='agent-ee.reference-circuit/1',
                                                                  part_number=request['part_number'], **report['circuit']))
        for rel, data in published.items():
            (staging / rel).write_bytes(data)
        files = sorted(str(p.relative_to(staging)) for p in staging.rglob('*') if p.is_file())
        report['library'] = dict(path='library', files={rel: hashlib.sha256((staging / rel).read_bytes()).hexdigest()
                                                         for rel in files}, lib_id=f'{library}:{name}')
        rename_noreplace(staging, out / 'library')
        report['published'] = True
        return finish('ok', 'accepted', 'independent extractions agree and every pad value is a verified fact')
    except (ExtractionError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        report['published'] = False
        return finish('malformed', 'unknown', str(exc))


def _prompt(request, label):
    pdf = request['pdf']
    return (
        f'You are an isolated datasheet extractor ({label}). Another extractor works independently; you never see '
        'its output and it never sees yours. Read ONLY the captured PDF at /candidate/' + pdf['path'] +
        f' (sha256 {pdf["sha256"]}) for part {request["part_number"]}. Do not use any other source.\n'
        'Return the recommended PCB land pattern as a pad table, and optionally the typical-application circuit.\n'
        'Rules:\n'
        '- Millimetres as decimal text. Top view, KiCad axes (+x right, +y down), origin at the package centre '
        'as the land-pattern drawing dimensions it. Pads are axis-aligned: w is the x extent, h the y extent.\n'
        '- kind smd/thru_hole/np_thru_hole; shape rect/circle/oval; hole null for smd, else {shape,w,h}. '
        'An np_thru_hole pad has number "" and name "" and copper equal to its hole.\n'
        '- name is the datasheet pin name for that pad number.\n'
        '- cite every value: {fact, scale}. fact is the id of one of your facts; value = scale x fact value. '
        'w, h and hole sizes use scale "1" exactly. Coordinate facts must quote a single dimension declaration: '
        '"[Pad/Pin/Lead] pitch [symbol] value [mm]", "Row/Column spacing/span [symbol] value [mm]", or '
        '"Pad/Pin/Lead span [symbol] value [mm]" (optional words in brackets; case insensitive). '
        'Pitch scales are the geometric site indices i-(N-1)/2 in a complete centered row of N sites of the same '
        'pad kind and perpendicular coordinate; every site must match the pitch. Span scales are only "1/2" '
        'or "-1/2"; row spacing/span binds y, column spacing/span binds x. Other scales or quote labels are '
        'unsupported coverage. Only a coordinate of exactly 0 may be cited as null.\n'
        '- Each fact is kind "dimension", unit "mm", value spelled exactly as printed (a plain decimal), with '
        f'source path "{pdf["path"]}", sha256 "{pdf["sha256"]}", the one-based physical page, a locator, and a '
        'quote copied exactly from `pdftotext -layout` output of that page that contains the value as a complete '
        'token. Verify your quotes with pdftotext before answering.\n'
        '- circuit components {ref,type,value} with exactly one type "device" for this part; nets {name,pins} '
        'with pins "REF.PIN"; device pins are pad numbers. Use null when the datasheet shows no typical circuit.\n'
        '- If a value is not printed in the PDF, leave its citation null and say so in limitations; never guess.\n'
        f'Answer with schema {EXTRACTION}.')


def run_extractions(request, pdf_path, run_dir, *, cancellation=None):
    """Two separate isolated agent invocations; each record is retained, success or failure."""
    from . import reviewers
    validate_request(request)
    data = Path(pdf_path).read_bytes()
    if hashlib.sha256(data).hexdigest() != request['pdf']['sha256'] or not data.startswith(b'%PDF-'):
        raise ExtractionError('PDF bytes differ from the request hash')
    run = Path(run_dir)
    if run.exists() or run.is_symlink():
        raise ExtractionError('extraction run directory must be new')
    run.mkdir(mode=0o700, parents=True)
    # Only the PDF is mounted (as /candidate); retained extractions stay outside every mount.
    target = run / 'inputs' / request['pdf']['path']
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    write_json(run / 'request.json', request)
    request_sha = identity(request)
    records = {}
    for label in LABELS:
        definition = request['extractors'][label]
        try:
            call, value = reviewers.dispatch(run, label, definition, _prompt(request, label), EXTRACTION_SCHEMA,
                                             definition['timeout_s'], cancellation=cancellation)
        except (OSError, ValueError, RuntimeError) as exc:
            call, value = dict(execution='failed', error=f'{type(exc).__name__}: {exc}'), None
        if call.get('execution') != 'ok':
            value = None
        records[label] = dict(schema=RECORD, label=label, request_sha256=request_sha, definition=definition,
                              call=call, value=value)
        write_json(run / 'extractions' / f'{label}.json', records[label])
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m agent_ee.datasheet_footprint', description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    extract = sub.add_parser('extract', help='run two isolated agent extractions and retain them')
    extract.add_argument('request')
    extract.add_argument('--pdf', required=True)
    extract.add_argument('--run', required=True)
    replay = sub.add_parser('accept', help='replay retained extractions without model calls')
    replay.add_argument('run')
    replay.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'extract':
            records = run_extractions(read_json(args.request), args.pdf, args.run)
            print(json.dumps({label: r['call'].get('execution') for label, r in records.items()}, sort_keys=True))
            return 0 if all(r['value'] is not None for r in records.values()) else 1
        report = accept(args.run, args.out)
    except (ExtractionError, OSError, json.JSONDecodeError) as exc:
        print(f'refused: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({k: report[k] for k in ('execution', 'decision', 'published', 'reason')}, sort_keys=True))
    return 0 if report['published'] else 1


if __name__ == '__main__':
    sys.exit(main())
