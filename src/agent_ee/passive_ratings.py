"""Source-derived passive rating fields from captured catalog rows and cited datasheet facts.

Ratings are never defaulted. A value comes from the purchased LCSC row of a
hash-bound captured JLCPCB response, or from a datasheet fact whose citation the
pinned pdftotext verifies on the selected (AGENTEE-237) datasheet source.
Contradictory, rejected or unselected sources refuse; an absent rating stays
missing so ``assembly:passive-ratings`` keeps reporting it.
"""
import hashlib
import re
import tempfile
from fractions import Fraction
from pathlib import Path

from .catalog import exact_fields, strict_json

SCHEMA = 'agent-ee.passive-rating-sources/1'
BINDING = 'agent-ee.passive-rating-binding/1'
PROPERTY = 'AGENT_EE_Ratings'
MAX_FIELD = 256
UNITS = {'Power': 'W', 'Voltage': 'V'}
# Exact JLCPCB attribute names only. A resistor's 'Voltage-Supply(Max)' is not
# mapped: its working/overload meaning is not established by the attribute name.
CATALOG_ATTRIBUTES = {'R': {'Power(Watts)': 'Power'}, 'C': {'Voltage Rating': 'Voltage'}}
FACT_KINDS = {'R': {'power_rating': 'Power', 'voltage_rating': 'Voltage'}, 'C': {'voltage_rating': 'Voltage'}}
PREFIXES = {'': 1, 'p': Fraction(1, 10**12), 'n': Fraction(1, 10**9), 'u': Fraction(1, 10**6),
            'µ': Fraction(1, 10**6), 'm': Fraction(1, 1000), 'k': 1000, 'M': 10**6}
QUANTITY_RE = re.compile(r'([0-9]+(?:\.[0-9]+)?|[0-9]+/[1-9][0-9]*)\s?([pnuµmkM]?)([VW])')
LIMITATION = ('Rated value from the cited source only; no derating, applied stress, effective capacitance, '
              'temperature suitability, ordering-suffix correspondence or engineering acceptance is established.')


def quantity(text, field):
    """Exact SI value of a rating spelling such as '100mW', '25V' or '1/10 W'; None otherwise."""
    match = QUANTITY_RE.fullmatch(text) if isinstance(text, str) else None
    if match is None or match[3] != UNITS[field]:
        return None
    value = Fraction(match[1]) * PREFIXES[match[2]]
    denominator = value.denominator
    for factor in (2, 5):
        while denominator % factor == 0:
            denominator //= factor
    # Only positive exact decimals; a repeating fraction such as 1/3 W is not a rating spelling.
    return value if value > 0 and denominator == 1 else None


def _decimal(value):
    """Canonical decimal text of an exact SI value accepted by quantity()."""
    whole, rest = divmod(value.numerator, value.denominator)
    digits = []
    while rest:
        rest *= 10
        digits.append(str(rest // value.denominator))
        rest %= value.denominator
    return str(whole) + ('.' + ''.join(digits) if digits else '')


def load_sources(root, declaration):
    """Validate the captured declaration and return (facts, extractor, dependencies)."""
    from .datasheet_facts import validate_extractor, validate_fact_records
    from .parts_manifest import _blob, _read_blob
    exact_fields(declaration, ('schema', 'catalog', 'facts', 'extractor'))
    if declaration['schema'] != SCHEMA or type(declaration['catalog']) is not bool:
        raise ValueError('expected agent-ee.passive-rating-sources/1 with boolean catalog')
    if declaration['facts'] is None:
        if declaration['extractor'] is not None:
            raise ValueError('rating extractor declared without facts')
        return [], None, {}
    _blob(declaration['facts'])
    validate_extractor(declaration['extractor'])
    document = strict_json(_read_blob(root, declaration['facts']))
    exact_fields(document, ('schema', 'facts'))
    if document['schema'] != 'agent-ee.datasheet-facts/1' or not isinstance(document['facts'], list) \
            or not document['facts']:
        raise ValueError('rating facts require nonempty agent-ee.datasheet-facts/1')
    validate_fact_records(document['facts'])
    kinds = {kind for table in FACT_KINDS.values() for kind in table}
    other = [f['id'] for f in document['facts'] if f['kind'] not in kinds]
    if other:
        raise ValueError('rating facts file holds non-rating facts: ' + ', '.join(other))
    return document['facts'], declaration['extractor'], {declaration['facts']['path']: declaration['facts']['sha256']}


def verify_citations(root, facts, extractor):
    """Fact id -> (status, reason) and PDF dependencies from the pinned pdftotext in the existing sandbox."""
    from .datasheet_facts import EXTRACTOR, page_support, split_pages, value_in_quote, verify_extractor
    from . import native_run
    from .parts_manifest import read_captured
    verify_extractor(extractor)
    results, pages, dependencies = {}, {}, {}
    with tempfile.TemporaryDirectory(prefix='agent-ee-ratings-') as scratch:
        version = dict(native_run.run(native_run.Intent('probe', [EXTRACTOR, '-v'], timeout=60), inputs=root, work=Path(scratch) / 'version'))
        lines = (Path(scratch) / 'version/console.log').read_text(errors='replace').splitlines()
        if version['execution'] != 'ok' or not lines or lines[0] != extractor['version']:
            raise ValueError('installed pdftotext version unavailable or differs from the declaration')
        for index, fact in enumerate(facts):
            source = fact['source']
            if hashlib.sha256(read_captured(root, source['path'])).hexdigest() != source['sha256']:
                results[fact['id']] = ('rejected', 'pdf_hash_mismatch')
                continue
            dependencies[source['path']] = source['sha256']
            if not value_in_quote(fact['value'], fact['unit'], source['quote']):
                results[fact['id']] = ('rejected', 'value_absent_from_quote')
                continue
            if source['path'] not in pages:
                work = Path(scratch) / f'pdf-{index}'
                record = dict(native_run.run(native_run.Intent('probe', [EXTRACTOR, '-layout', '/candidate/' + source['path'],
                                              '/work/document.txt'], timeout=60), inputs=root, work=work))
                # A failed extraction is never support: the fact stays unverified and refuses on use.
                pages[source['path']] = (split_pages((work / 'document.txt').read_text(encoding='utf-8'))
                                         if record['execution'] == 'ok' and (work / 'document.txt').is_file()
                                         else 'pdftotext ' + record['execution'])
            text = pages[source['path']]
            results[fact['id']] = ('unsupported', text) if isinstance(text, str) else page_support(source, text)
    verify_extractor(extractor)
    return results, dependencies


def _catalog_sources(root, part, kind):
    """Rating attributes of the exact purchased row in the record's captured response."""
    from .part_record import captured_attributes
    selection = part.get('selection')
    if not isinstance(selection, dict) or selection.get('kind') != 'named':
        return [], 'no captured supplier row for a ' + (selection or {}).get('kind', 'generic') + ' part'
    attributes, origin = captured_attributes(root, part)
    if attributes is None:
        return [], f'captured {part["lcsc"]} row has null attributes'
    path, sha = origin['path'], origin['sha256']
    sources = []
    for attribute in attributes:
        field = CATALOG_ATTRIBUTES[kind].get(attribute.get('attribute_name_en'))
        if field is not None:
            sources.append({'field': field, 'value': attribute.get('attribute_value_name'),
                            'source': {'kind': 'catalog', 'id': part['lcsc'], 'path': path, 'sha256': sha,
                                       'attribute': attribute['attribute_name_en']}})
    reason = None if sources else f'captured {part["lcsc"]} row has no {"/".join(CATALOG_ATTRIBUTES[kind])} attribute'
    return sources, reason


def _fact_sources(root, part, kind, facts, verified):
    """Verified citations on the part's selected datasheet; other PDFs refuse."""
    from .datasheet_selection import selected_source
    mine = [f for f in facts if f['part_number'] == part['mpn']]
    if not mine:
        return []
    sheet = part.get('datasheet', {})
    if sheet.get('status') != 'captured' or 'source_selection' not in sheet:
        raise ValueError(f'{part["id"]}: datasheet rating facts {[f["id"] for f in mine]} require a '
                         'captured datasheet with a declared source_selection')
    selected, rejected = selected_source(root, sheet['source_selection'], context=part['id'])
    sources = []
    for fact in mine:
        source = fact['source']
        if any(r['sha256'] == source['sha256'] for r in rejected):
            raise ValueError(f'{part["id"]}: fact {fact["id"]} cites rejected source {source["sha256"]}')
        if (source['sha256'] != selected['sha256'] or
                {'path': source['path'], 'sha256': source['sha256']} != sheet['artifact']):
            raise ValueError(f'{part["id"]}: fact {fact["id"]} cites {source["path"]} ({source["sha256"]}), '
                             f'not selected source {selected["id"]} ({selected["sha256"]})')
        if fact['kind'] not in FACT_KINDS[kind]:
            raise ValueError(f'{part["id"]}: fact {fact["id"]} kind {fact["kind"]} is not a {kind} rating')
        status, reason = verified[fact['id']]
        if status != 'verified':
            raise ValueError(f'{part["id"]}: fact {fact["id"]} citation {status}: {reason}')
        sources.append({'field': FACT_KINDS[kind][fact['kind']], 'value': fact['value'] + fact['unit'],
                        'source': {'kind': 'datasheet', 'id': selected['id'], 'fact': fact['id'],
                                   'path': source['path'], 'sha256': source['sha256'], 'page': source['page']}})
    return sources


def derive(root, part, kind, *, catalog=True, facts=(), verified=None):
    """Return {'fields', 'record'} or {'missing': reason}; contradictions raise with exact source ids."""
    from .copper_features import require_purchased
    require_purchased(part, 'passive ratings')
    if kind not in CATALOG_ATTRIBUTES:
        raise ValueError('passive rating kind must be R or C')
    sources, reasons = [], []
    if catalog:
        found, reason = _catalog_sources(root, part, kind)
        sources += found
        if reason:
            reasons.append(reason)
    else:
        reasons.append('catalog ratings not declared')
    sources += _fact_sources(root, part, kind, list(facts), verified or {})
    ratings = {}
    for item in sources:
        value = quantity(item['value'], item['field'])
        if value is None:
            raise ValueError(f'{part["id"]}: unparsed {item["field"]} rating {item["value"]!r} from {item["source"]}')
        ratings.setdefault(item['field'], []).append((value, item))
    fields, record = {}, {}
    for field, found in sorted(ratings.items()):
        values = {value for value, _ in found}
        if len(values) != 1:
            raise ValueError(f'{part["id"]}: contradictory {field} sources ' +
                             str([(i['value'], i['source']) for _, i in found]))
        # Catalog spelling first, so a catalog value is never respelled by a later fact.
        spelling = sorted(found, key=lambda pair: pair[1]['source']['kind'] != 'catalog')[0][1]['value']
        fields[field] = spelling
        record[field] = {'value': spelling, 'si': _decimal(values.pop()), 'unit': UNITS[field],
                         'sources': [i['source'] for _, i in found]}
    if not fields:
        return {'missing': '; '.join(reasons + ['no selected-source datasheet rating fact'])}
    from .storage import canonical, identity
    full = {'schema': BINDING, 'part': {'id': part['id'], 'lcsc': part.get('lcsc'), 'mpn': part['mpn']},
            'ratings': record, 'limitations': [LIMITATION]}
    # Native fields are limited to 256 characters; the field pins the full receipted record by hash.
    fields[PROPERTY] = canonical({'schema': BINDING, 'ratings': dict(fields),
                                  'record_sha256': identity(full)}).decode()
    if len(fields[PROPERTY]) > MAX_FIELD:
        raise ValueError(f'{part["id"]}: rating provenance field exceeds {MAX_FIELD} characters')
    return {'fields': fields, 'record': full, 'record_sha256': identity(full)}


def validate_binding(props, kind):
    """Problems with a native rating provenance field; an absent field yields none."""
    raw = props.get(PROPERTY)
    if raw is None:
        return []
    try:
        document = strict_json(raw.encode() if isinstance(raw, str) else b'')
        exact_fields(document, ('schema', 'ratings', 'record_sha256'))
        if (document['schema'] != BINDING or not isinstance(document['ratings'], dict) or
                not document['ratings'] or not re.fullmatch('[0-9a-f]{64}', str(document['record_sha256']))):
            raise ValueError('unsupported rating provenance')
    except (ValueError, TypeError) as exc:
        return [f'{PROPERTY} is malformed: {exc}']
    problems = []
    allowed = set(CATALOG_ATTRIBUTES[kind].values()) | set(FACT_KINDS[kind].values())
    for field, value in sorted(document['ratings'].items()):
        if field not in allowed:
            problems.append(f'{field} is not a {kind} rating')
        elif quantity(value, field) is None or props.get(field) != value:
            problems.append(f'{field}={props.get(field)!r} differs from provenance {value!r}')
    return problems


def load_provenance(root, declaration):
    """Derived records of a receipted rating producer; ValueError when unavailable.

    ``declaration`` names the captured stage receipt of the binding-fill step, its
    coordinator-pinned sha256, and the captured ``binding-fill.json``. The report is
    accepted only as the exact artifact the adoptable receipt recorded.
    """
    from .stage_receipts import ADOPTABLE, RECEIPT_KEYS, RECEIPT_SCHEMAS, legacy_source_refusal, validate_step
    from .storage import digest, identity
    root = Path(root)
    receipt_path, report_path = root / declaration['receipt'], root / declaration['report']
    for path in (receipt_path, report_path):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'rating provenance file is missing: {path.relative_to(root).as_posix()}')
    if digest(receipt_path) != declaration['receipt_sha256']:
        raise ValueError('binding-fill receipt differs from its pinned sha256')
    receipt = strict_json(receipt_path.read_bytes())
    if (not isinstance(receipt, dict) or receipt.get('schema') not in RECEIPT_SCHEMAS or set(receipt) != RECEIPT_KEYS or
            identity({k: v for k, v in receipt.items() if k != 'identity'}) != receipt['identity']):
        raise ValueError('malformed binding-fill stage receipt')
    legacy_source_refusal(receipt)
    spec = validate_step(receipt['spec'], host=False)
    if spec != receipt['spec'] or spec['step'] not in ('binding-fill', 'compile-parts') or '--ratings' not in spec['argv']:
        raise ValueError('stage receipt is not a rating producer with declared ratings')
    if receipt['status'] not in ADOPTABLE:
        raise ValueError(f'binding-fill receipt status {receipt["status"]} is not adoptable')
    artifact = 'out/report.json' if spec['step'] == 'compile-parts' else 'out/binding-fill.json'
    if not isinstance(receipt['artifacts'], dict) or receipt['artifacts'].get(artifact) != digest(report_path):
        raise ValueError('binding-fill report is not the receipted artifact')
    report = strict_json(report_path.read_bytes())
    if spec['step'] == 'compile-parts':
        from .parts_stage import SCHEMA as PARTS_SCHEMA
        if (not isinstance(report, dict) or report.get('schema') != PARTS_SCHEMA or
                report.get('status') != 'produced' or not report.get('ratings_source') or
                not isinstance(report.get('output_sha256'), str) or
                report['output_sha256'] not in receipt['outputs'].values()):
            raise ValueError('expected produced parts report bound to an adopted output')
        report = report.get('binding_report')
    if not isinstance(report, dict) or report.get('schema') not in ('agent-ee.binding-fill/1', 'agent-ee.binding-fill/2'):
        raise ValueError('expected agent-ee.binding-fill/1 or /2 report')
    if report['schema'] == 'agent-ee.binding-fill/2' and (
            '--source-migration' not in spec['argv'] or not isinstance(report.get('probe_migrations'), list)):
        raise ValueError('binding-fill/2 requires explicit source migration evidence')
    ratings = report.get('ratings')
    if not isinstance(ratings, dict) or not isinstance(ratings.get('derived'), dict):
        raise ValueError('binding-fill report carries no ratings provenance')
    for ref, entry in ratings['derived'].items():
        exact_fields(entry, ('record', 'record_sha256'))
        if (not isinstance(entry['record'], dict) or entry['record_sha256'] != identity(entry['record']) or
                entry['record'].get('schema') != BINDING):
            raise ValueError(f'receipted rating record for {ref} does not match its identity')
    return ratings['derived']


def resolve_record(props, ref, derived, sheet='/', sheet_path=None):
    """Problems binding a native rating field to the receipted record of ``ref``.

    A component outside the root sheet resolves first by its hierarchical path
    UUID occurrence path, then the legacy sheet-name path. Conflicting records
    for those two identities refuse; names never override a native occurrence.
    """
    raw = props.get(PROPERTY)
    keys = list(dict.fromkeys(path + ref for path in (sheet_path, sheet)
                              if path not in (None, '/') and path + ref in derived))
    if keys:
        if any(derived[key] != derived[keys[0]] for key in keys[1:]):
            return [f'conflicting receipted rating records for {ref} sheet name and UUID occurrence']
        ref = keys[0]
    entry = derived.get(ref)
    if raw is None:
        return [] if entry is None else [f'receipted rating record for {ref} is absent from the component']
    if entry is None:
        return [f'{PROPERTY} names no receipted rating record for {ref}']
    document = strict_json(raw.encode())
    record = entry['record']
    problems = []
    if document['record_sha256'] != entry['record_sha256']:
        problems.append(f'record_sha256 {document["record_sha256"]} is not the receipted record '
                        f'{entry["record_sha256"]}')
    expected = {field: rating['value'] for field, rating in record['ratings'].items()}
    if document['ratings'] != expected:
        problems.append(f'ratings {document["ratings"]} differ from the receipted record {expected}')
    part = record['part']
    if part.get('lcsc') is not None and props.get('LCSC') != part['lcsc']:
        problems.append(f'LCSC={props.get("LCSC")!r} differs from the receipted record {part["lcsc"]!r}')
    numbers = [props[name] for name in ('MPN', 'Partnumber', 'PartNumber') if name in props]
    if not numbers or any(number != part['mpn'] for number in numbers):
        problems.append(f'part number differs from the receipted record {part["mpn"]!r}')
    return problems
