"""Textual citation support and an explicitly scoped native symbol pin consumer."""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires

import copy
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

from .candidate import safe_relative
from .storage import digest, identity, read_json, read_verified, write_json

EXTRACTOR = '/usr/bin/pdftotext'


def load_facts(inputs, rel):
    path = inputs / str(safe_relative(rel))
    data = read_json(path)
    opted_in = isinstance(data, dict) and data.get('schema') == 'agent-ee.datasheet-facts/2'
    if opted_in and not isinstance(data.get('facts'), list):
        raise ValueError('facts must be a list')
    if not opted_in and (not isinstance(data, dict) or set(data) != {'schema', 'facts'} or
            data['schema'] != 'agent-ee.datasheet-facts/1' or
            not isinstance(data['facts'], list) or not data['facts']):
        raise ValueError('expected nonempty agent-ee.datasheet-facts/1')
    validate_fact_records(data['facts'])
    if opted_in:
        load_obligations(inputs, rel, data=data)
    return data['facts']


def validate_fact_records(facts):
    """Shape of every fact record; shared by captured files and retained extractions."""
    ids = set()
    for fact in facts:
        if not isinstance(fact, dict):
            raise ValueError('fact must be an object')
        for key in ('id', 'part_number', 'kind', 'value', 'conditions'):
            if not isinstance(fact.get(key), str) or not fact[key].strip():
                raise ValueError(f'fact needs nonempty {key}')
        if fact['id'] in ids:
            raise ValueError('duplicate fact id')
        ids.add(fact['id'])
        if not isinstance(fact.get('unit'), str):
            raise ValueError('fact needs unit string (empty for pin functions)')
        if fact['kind'] == 'pin_function' and (
                not isinstance(fact.get('pin'), str) or not fact['pin'].strip()):
            raise ValueError('pin_function needs pin string')
        source = fact.get('source')
        if not isinstance(source, dict) or set(source) != {'path', 'sha256', 'page', 'locator', 'quote'}:
            raise ValueError('fact source needs path, sha256, page, locator and exact quote')
        safe_relative(source['path'])
        if not isinstance(source['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', source['sha256']):
            raise ValueError('fact source needs SHA-256')
        if type(source['page']) is not int or source['page'] < 1:
            raise ValueError('fact source page must be a positive integer')
        for key in ('locator', 'quote'):
            if not isinstance(source[key], str) or not source[key].strip():
                raise ValueError(f'fact source needs {key}')


def load_acquisition(inputs, locator, capture_bindings, reads=None):
    """Replay the shared AGENTEE-33 capture and all PDFs without network access.

    Every file is hashed and checked on the bytes it was read as (AGENTEE-454). reads,
    when given, is one validation call's storage.CallReads: a replay already done in that
    call for the same resolved root and capture, pinned digest and bindings is reused, and
    every captured path's link and containment policy is checked again at this use."""
    from .catalog import exact_fields
    from .datasheet_parts import captured_path, _hash
    exact_fields(locator, ('path', 'sha256'))
    if reads is None:
        return _load_acquisition(inputs, locator, capture_bindings, None)
    source = captured_path(inputs, locator['path'])
    try:
        key = ('load_acquisition', os.path.realpath(inputs), os.path.realpath(source),
               _hash(locator['sha256']), identity(capture_bindings))
    except (TypeError, ValueError):
        return _load_acquisition(inputs, locator, capture_bindings, reads)
    acquired, dependencies = reads.result(
        key, lambda: _load_acquisition(inputs, locator, capture_bindings, reads))
    for rel in dependencies:
        captured_path(inputs, rel)
    return copy.deepcopy(acquired), set(dependencies)


def _load_acquisition(inputs, locator, capture_bindings, reads):
    from .catalog import exact_fields, nonempty
    from .datasheet_parts import captured_path, _hash
    source = captured_path(inputs, locator['path'])
    expected = _hash(locator['sha256'])
    raw, found = read_verified(source, expected, reads)
    if found != expected:
        raise ValueError('acquisition hash mismatch')
    acquisition = json.loads(raw.decode())
    dependencies = {locator['path']}
    by_id = {p['id']: p['datasheet_url'] for p in capture_bindings}
    if len(by_id) != len(capture_bindings):
        raise ValueError('duplicate acquisition binding')
    exact_fields(acquisition, ('schema', 'bindings_sha256', 'outcome', 'parts'))
    historical = acquisition['schema'] == 'agent-ee.datasheet-capture/2'
    if (acquisition['schema'] not in ('agent-ee.datasheet-capture/1', 'agent-ee.datasheet-capture/2') or
            acquisition['bindings_sha256'] != identity(capture_bindings)):
        raise ValueError('acquisition binding identity mismatch')
    if not isinstance(acquisition['parts'], list) or len(acquisition['parts']) != len(by_id):
        raise ValueError('acquisition must cover every binding')
    acquired, directory = {}, Path(locator['path']).parent
    for record in acquisition['parts']:
        fields = ('id', 'url', 'final_url', 'fetched_at', 'sha256', 'path', 'missing', 'reason')
        if historical:
            fields += ('acquisition_method', 'redirect_provenance', 'source_note', 'captured_at')
        exact_fields(record, fields)
        if historical:
            from .datasheet_capture import historical_source
            if (record['acquisition_method'] != 'historical_import' or
                    record['redirect_provenance'] != 'unknown' or record['final_url'] is not None or
                    record['missing'] is not False):
                raise ValueError('historical import cannot claim live capture or known redirect')
            note = record['source_note']
            exact_fields(note, ('path', 'sha256', 'entry'))
            note_rel = str(directory / str(safe_relative(note['path'])))
            entry = historical_source(inputs, {**note, 'path': note_rel}, record['sha256'], reads)
            if (record['url'] != entry['url'] or record['captured_at'] != entry['captured_at_utc'] or
                    record['fetched_at'] != entry.get('fetched_at')):
                raise ValueError('historical import contradicts source note')
            dependencies.add(note_rel)
        ref = record['id']
        if not isinstance(ref, str) or ref in acquired or ref not in by_id or record['url'] != by_id[ref]:
            raise ValueError('acquisition reference/URL mismatch')
        for key in ('final_url', 'fetched_at'):
            if record[key] is not None and not isinstance(record[key], str):
                raise ValueError(f'acquisition {key} must be text or null')
        if type(record['missing']) is not bool:
            raise ValueError('acquisition missing must be boolean')
        if record['missing']:
            if record['path'] is not None or record['sha256'] is not None:
                raise ValueError('missing acquisition cannot claim a PDF')
            nonempty(record['reason'])
        else:
            from .datasheet_capture import validate_url
            validate_url(record['url'])
            if not historical:
                validate_url(record['final_url'])
                nonempty(record['fetched_at'])
            if record['reason'] is not None:
                raise ValueError('successful acquisition cannot have missing reason')
            pdf_rel = str(directory / str(safe_relative(record['path'])))
            pdf = captured_path(inputs, pdf_rel)
            expected = _hash(record['sha256'])
            # One read: the envelope is checked on exactly the bytes that were hashed.
            raw, found = read_verified(pdf, expected, reads)
            if found != expected:
                raise ValueError('successful acquisition PDF hash mismatch')
            if not raw.startswith(b'%PDF-') or not raw.rstrip().endswith(b'%%EOF'):
                raise ValueError('successful acquisition is not a PDF envelope')
            dependencies.add(pdf_rel)
        acquired[ref] = (record if historical else
                         {**record, 'acquisition_method': 'live',
                          'redirect_provenance': 'observed' if record['final_url'] is not None else 'unknown'})
    expected_outcome = 'missing' if any(r['missing'] for r in acquired.values()) else 'captured'
    if historical:
        expected_outcome = 'imported'
    if acquisition['outcome'] != expected_outcome:
        raise ValueError('acquisition outcome contradicts records')
    return acquired, dependencies


def load_obligations(inputs, rel, *, data=None):
    """Validate opt-in captured per-part obligations; facts/1 remains unchanged."""
    from .catalog import exact_fields, nonempty
    from .datasheet_parts import captured_path, validate_bindings, _hash
    inputs = Path(inputs)
    data = read_json(captured_path(inputs, rel)) if data is None else data
    if not isinstance(data, dict) or data.get('schema') != 'agent-ee.datasheet-facts/2':
        return None
    exact_fields(data, ('schema', 'bindings', 'acquisition', 'required_parts', 'facts'))
    if not isinstance(data['facts'], list):
        raise ValueError('facts must be a list')
    dependencies = set()
    documents = {}
    for name in ('bindings', 'acquisition'):
        locator = data[name]
        exact_fields(locator, ('path', 'sha256'))
        path = captured_path(inputs, locator['path'])
        if digest(path) != _hash(locator['sha256']):
            raise ValueError(f'{name} hash mismatch')
        dependencies.add(str(safe_relative(locator['path'])))
        documents[name] = read_json(path)
    bindings = validate_bindings(documents['bindings'])
    by_ref = {p['reference']: p for p in bindings['parts']}
    required = data['required_parts']
    if not isinstance(required, list) or len(required) != len(by_ref):
        raise ValueError('required parts must cover complete bindings')
    seen = set()
    for part in required:
        exact_fields(part, ('reference', 'selection_id', 'part_number', 'required_facts'))
        ref = nonempty(part['reference'])
        if ref in seen or ref not in by_ref:
            raise ValueError('duplicate or unknown required reference')
        seen.add(ref)
        if any(part[k] != by_ref[ref][k] for k in ('selection_id', 'part_number')):
            raise ValueError('required part identity mismatch')
        ids = part['required_facts']
        if (not isinstance(ids, list) or not ids or any(not isinstance(fid, str) or not fid.strip() for fid in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('part needs distinct nonempty required facts')
    capture_bindings = sorted([{'id': p['reference'], 'datasheet_url': p['datasheet_url']}
                               for p in bindings['parts']], key=lambda p: p['id'])
    acquired, acquisition_dependencies = load_acquisition(inputs, data['acquisition'], capture_bindings)
    dependencies.update(acquisition_dependencies)
    directory = Path(data['acquisition']['path']).parent
    # A cited fact must belong to an explicit part obligation and that part's PDF.
    declared = {fid for p in required for fid in p['required_facts']}
    for fact in data['facts']:
        if (not isinstance(fact, dict) or not isinstance(fact.get('id'), str) or
                fact['id'] not in declared or not isinstance(fact.get('source'), dict)):
            raise ValueError('fact is not a declared obligation')
        applicable = [p for p in required if fact['id'] in p['required_facts']]
        for part in applicable:
            record = acquired[part['reference']]
            expected_path = str(directory / str(record['path']))
            if (record['missing'] or part['part_number'] is None or
                    fact.get('part_number') != part['part_number'] or
                    fact.get('source', {}).get('path') != expected_path or
                    fact.get('source', {}).get('sha256') != record['sha256']):
                raise ValueError('citation does not match required part acquisition/applicability')
    return {**data, 'dependencies': dependencies, 'acquired': acquired,
            'qualification': bindings['qualification']}


def obligation_records(envelope, facts):
    """One result per required part, including absent PDFs and absent citations."""
    verified = {r['id'] for r in facts if r['status'] == 'verified'}
    records = []
    for part in envelope['required_parts']:
        acquisition = envelope['acquired'][part['reference']]
        missing = [fid for fid in part['required_facts'] if fid not in verified]
        reason = ('datasheet_missing' if acquisition['missing'] else
                  'part_number_unknown' if part['part_number'] is None else
                  'citations_missing_or_unverified' if missing else 'declared_citations_verified')
        records.append({**part, 'status': 'verified' if reason == 'declared_citations_verified' else 'unsupported',
                        'reason': reason, 'missing_facts': missing})
    return records


# Signs belong to a written literal (-5, ±5, 1e-5); each spelling is distinct, never normalized.
SIGNS = '+-\u00b1\u2010\u2011\u2212\u2213\ufe62\ufe63\uff0b\uff0d'
# Dashes separate a range between digits (4.5–5.5) but are a minus sign elsewhere (–0.3).
DASHES = '\u2012\u2013\u2014\u2015'
# Superscript signs and multiplication signs continue a written number (10⁻⁵, 5×10³).
JOINERS = '\u207a\u207b\u00d7\u00b7\u22c5'


def joined(char):
    """Word characters, combining marks, format characters and unmapped glyphs continue a token."""
    return char is not None and (re.match(r'\w', char) is not None or char == '\ufffd' or
                                 unicodedata.category(char) in ('Mn', 'Mc', 'Me', 'Cf', 'Co', 'Cn'))


def numeric(char):
    return char is not None and (char.isdecimal() or char == '.')


def starts_token(quote, start, value):
    before = quote[start - 1] if start else None
    previous = quote[start - 2] if start > 1 else None
    if before is None or joined(before):
        return before is None
    if value[0] in SIGNS + DASHES:
        return before not in SIGNS + DASHES + JOINERS + '.,'
    if not numeric(value[0]):
        return True
    if before in DASHES:
        return previous is not None and previous.isdecimal()
    return not (before in SIGNS + JOINERS + '.' or (before == ',' and numeric(previous)))


def ends_token(quote, end, value):
    after = quote[end] if end < len(quote) else None
    following = quote[end + 1] if end + 1 < len(quote) else None
    if after is None or joined(after):
        return after is None
    if not numeric(value[-1]):
        return True
    if after in '.,':
        return not (numeric(following) or joined(following))
    if after in SIGNS:
        # Glued sign: a range, a difference or a flattened superscript exponent (10-5).
        return not numeric(following)
    return after not in JOINERS


def value_in_quote(value, unit, quote):
    """Complete written token, optionally followed by the declared unit.

    CAN is not in CANL or CAN with a combining mark; 5 is not in 15, 4.5, .5, -5, −5
    or 1e-5; 3 is not in 5E+3. Spelling, including sign and exponent, is compared exactly.
    """
    if (not value or value != value.strip() or
            unicodedata.category(value[0]).startswith('M')):
        return False
    suffix = re.compile(r'\s*' + re.escape(unit)) if unit.strip() else None
    exponent = re.compile(f'[eE][{re.escape(SIGNS)}]?\\d')
    start = quote.find(value)
    while start >= 0:
        end = start + len(value)
        if starts_token(quote, start, value) and not (
                numeric(value[-1]) and exponent.match(quote, end)):
            if ends_token(quote, end, value):
                return True
            attached = suffix.match(quote, end) if suffix else None
            if attached and ends_token(quote, attached.end(), unit):
                return True
        start = quote.find(value, start + 1)
    return False


def split_pages(text):
    """Poppler separates physical PDF pages with form feeds, including a final delimiter."""
    pages = text.split('\f')
    if pages and pages[-1] == '':
        pages.pop()
    return pages


def page_support(source, pages):
    """Status and reason of an exact, token-bounded quote on its cited physical page."""
    if source['page'] > len(pages):
        return 'rejected', 'page_out_of_range'
    if not pages[source['page'] - 1].strip():
        return 'unsupported', 'page_has_no_text'
    page, quote = pages[source['page'] - 1], source['quote']
    start = page.find(quote)
    if start < 0:
        return 'rejected', 'quote_absent_from_page'
    # Preserve the exact quote match; edge whitespace already delimits its tokens.
    tokens = quote.strip()
    leading = len(quote) - len(quote.lstrip())
    trailing = len(quote.rstrip())
    while start >= 0:
        if starts_token(page, start + leading, tokens) and ends_token(page, start + trailing, tokens):
            return 'verified', 'exact_quote_and_value_supported'
        start = page.find(quote, start + 1)
    return 'rejected', 'quote_truncates_a_page_token'


def verification_inputs(scope, inputs):
    """Only new fact checks opt in; historical baseline compilation is unchanged."""
    paths = set()
    for spec in scope.get('checks', []):
        if spec.get('kind') == 'datasheet_facts':
            if inputs is None:
                raise ValueError('datasheet baseline compilation requires captured inputs')
            rel = str(safe_relative(spec['params']['facts']))
            paths.add(rel)
            paths.update(str(safe_relative(f['source']['path'])) for f in load_facts(Path(inputs), rel))
            envelope = load_obligations(Path(inputs), rel)
            if envelope is not None:
                paths.update(envelope['dependencies'])
    return paths


def validate_extractor(tool):
    if (not isinstance(tool, dict) or set(tool) != {'path', 'sha256', 'version'} or
            tool['path'] != EXTRACTOR or not isinstance(tool['version'], str) or
            not tool['version'].startswith('pdftotext version ') or
            not isinstance(tool['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', tool['sha256'])):
        raise ValueError('extractor requires /usr/bin/pdftotext, sha256 and version')


def verify_extractor(tool):
    validate_extractor(tool)
    if not Path(EXTRACTOR).is_file():
        raise ValueError('installed pdftotext unavailable')
    if digest(EXTRACTOR) != tool['sha256']:
        raise ValueError('installed pdftotext hash changed')


def extract(ctx, name, argv):
    from .checks import CheckError
    from .executor import execute
    work = ctx.work / name
    remaining = ctx.remaining()
    if remaining <= 1:
        raise CheckError('interrupted', 'deadline exhausted before PDF extraction')
    record = execute(ctx.inputs, work, argv, timeout=min(60, remaining))
    ctx.evidence(*(f'{name}/{p}' for p in ('execution.json', 'console.log', 'stdout.log', 'stderr.log')))
    if record['execution'] != 'ok':
        raise CheckError(record['execution'], f'pdftotext {name} {record["execution"]}')
    return work


def check_datasheet_facts(ctx):
    from .checks import CheckError
    try:
        facts = load_facts(ctx.inputs, ctx.params['facts'])
        envelope = load_obligations(ctx.inputs, ctx.params['facts'])
        if envelope:
            for rel in envelope['dependencies'] | {str(safe_relative(ctx.params['facts']))}:
                if ctx.manifest['files'].get(rel) != digest(ctx.inputs / rel):
                    raise ValueError('obligation dependency not captured or changed: ' + rel)
        tool = ctx.params['extractor']
        verify_extractor(tool)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise CheckError('malformed', str(exc)) from exc
    version_work = extract(ctx, 'version', [EXTRACTOR, '-v'])
    version_lines = (version_work / 'console.log').read_text().splitlines()
    if not version_lines:
        raise CheckError('malformed', 'pdftotext returned no version')
    version = version_lines[0]
    if version != tool['version']:
        raise CheckError('unsupported', 'installed pdftotext version differs from declaration')
    records = []
    for index, fact in enumerate(facts):
        source = fact['source']
        record = {'id': fact['id'], 'fact': fact, 'status': 'rejected', 'reason': ''}
        records.append(record)
        pdf = ctx.inputs / source['path']
        if source['path'] not in ctx.manifest['files'] or not pdf.is_file():
            record['reason'] = 'pdf_not_captured'
        elif digest(pdf) != source['sha256'] or ctx.manifest['files'][source['path']] != source['sha256']:
            record['reason'] = 'pdf_hash_mismatch'
        elif not value_in_quote(fact['value'], fact['unit'], source['quote']):
            record['reason'] = 'value_absent_from_quote'
        elif fact['kind'] == 'pin_function' and not value_in_quote(fact['pin'], '', source['quote']):
            record['reason'] = 'pin_absent_from_quote'
        else:
            work = extract(ctx, f'page-{index}', [EXTRACTOR, '-layout',
                ctx.candidate_path(source['path']), '/work/page.txt'])
            ctx.evidence(f'page-{index}/page.txt')
            if not (work / 'page.txt').is_file():
                raise CheckError('malformed', 'pdftotext wrote no page text')
            status, reason = page_support(source, split_pages((work / 'page.txt').read_text(encoding='utf-8')))
            record.update(status=status, reason=reason)
    try:
        verify_extractor(tool)
    except (ValueError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    obligations = obligation_records(envelope, records) if envelope else []
    report = {'facts': records, 'extractor': tool}
    if envelope:
        report.update(required_parts=obligations, qualification=envelope['qualification'],
                      acquisitions=envelope['acquired'])
    write_json(ctx.work / 'facts.json', report)
    ctx.evidence('facts.json')
    ctx.result['details'].update(facts=records, extractor=tool)
    ctx.result['limitations'].append('Citation verification proves text occurrence only; it does not '
        'establish part applicability, correct row, conditions, units or engineering interpretation.')
    if envelope:
        ctx.result['details'].update(required_parts=obligations, qualification=envelope['qualification'],
                                    acquisitions=envelope['acquired'])
    verified = all(r['status'] == 'verified' for r in records + obligations)
    ctx.result.update(execution='ok' if verified else 'unsupported',
                      outcome='pass' if verified else 'unknown',
                      summary='All datasheet citations verified' if verified else 'Unverified datasheet citations')


def prerequisite(ctx, cid, kind, artifact, *, allow_unsupported=False):
    from .checks import CheckError, SAFE_ID
    if not isinstance(cid, str) or not SAFE_ID.fullmatch(cid):
        raise CheckError('unsupported', f'{kind} prerequisite needs a safe check id')
    spec = next((s for s in ctx.scope['checks'] if s['id'] == cid), None)
    path = ctx.run_dir / 'checks' / f'{cid}.json'
    if not spec or spec['kind'] != kind or not path.is_file():
        raise CheckError('unsupported', f'{kind} prerequisite missing or not yet run')
    result = read_json(path)
    allowed = ('ok', 'unsupported') if allow_unsupported else ('ok',)
    if result.get('candidate_id') != ctx.manifest['candidate_id'] or result.get('execution') not in allowed:
        raise CheckError('unsupported', f'{kind} prerequisite unverified')
    rel = f'work/{cid}/{artifact}'
    if rel not in result['evidence'] or not (ctx.run_dir / rel).is_file():
        raise CheckError('unsupported', f'{kind} prerequisite lacks {artifact}')
    ctx.result['evidence'].append(rel)
    return ctx.run_dir / rel


def check_pin_map(ctx):
    from .checks import CheckError
    try:
        facts_path = prerequisite(ctx, ctx.params.get('facts_check'), 'datasheet_facts', 'facts.json')
        xml_path = prerequisite(ctx, ctx.params.get('netlist_check'), 'netlist', 'design.xml')
        facts_report = read_json(facts_path)
        acquisitions = facts_report.get('acquisitions', {})
        records = facts_report['facts']
        verified = {r['id']: r['fact'] for r in records if r['status'] == 'verified'}
        symbols = ctx.params['symbols']
        if not isinstance(symbols, list) or not symbols:
            raise ValueError('pin_map requires explicit engineer-supplied symbols applicability')
        root = ET.parse(xml_path).getroot()
        components = {c.get('ref'): c for c in root.findall('./components/comp')}
        libparts = {(p.get('lib'), p.get('part')): p for p in root.findall('./libparts/libpart')}
        comparisons, seen = [], set()
        for symbol in symbols:
            if (not isinstance(symbol, dict) or
                    set(symbol) != {'reference', 'part_number', 'facts'} or
                    not all(isinstance(symbol[k], str) and symbol[k].strip()
                            for k in ('reference', 'part_number')) or
                    not isinstance(symbol['facts'], list) or not symbol['facts'] or
                    any(not isinstance(fid, str) or not fid for fid in symbol['facts'])):
                raise ValueError('symbol needs reference, part_number and nonempty facts')
            ref = symbol['reference']
            if ref in seen:
                raise ValueError('duplicate symbol applicability')
            seen.add(ref)
            if ref not in components:
                raise CheckError('unsupported', f'native netlist has no component {ref}')
            comp = components[ref]
            lib = comp.find('libsource')
            part = libparts.get((lib.get('lib'), lib.get('part'))) if lib is not None else None
            if part is None:
                raise CheckError('unsupported', f'native netlist lacks symbol pin metadata for {ref}')
            pins = {}
            for native_pin in part.findall('./pins/pin'):
                pins.setdefault(native_pin.get('num'), []).append(native_pin.get('name'))
            checked = set()
            for fid in symbol['facts']:
                fact = verified.get(fid)
                if not fact or fact['kind'] != 'pin_function' or fact['part_number'] != symbol['part_number']:
                    raise CheckError('unsupported', f'{ref}: fact {fid!r} is unverified or inapplicable')
                pin = fact['pin']
                if pin in checked:
                    raise ValueError(f'{ref}: duplicate pin fact {pin}')
                checked.add(pin)
                names = pins.get(pin, [])
                if len(names) != 1 or not isinstance(names[0], str) or not names[0].strip():
                    raise CheckError('unsupported', f'{ref}: native pin {pin} metadata missing or ambiguous')
                native_name = names[0]
                comparisons.append({'reference': ref, 'pin': pin, 'fact_id': fid,
                    'expected': fact['value'], 'native_name': native_name,
                    'locator': f'component {ref}, pin {pin}, symbol {lib.get("lib")}:{lib.get("part")}',
                    'matches': native_name == fact['value']})
    except (KeyError, TypeError, ValueError, OSError, ET.ParseError) as exc:
        raise CheckError('malformed', str(exc)) from exc
    write_json(ctx.work / 'pin-map.json', {'comparisons': comparisons, 'acquisitions': acquisitions})
    ctx.evidence('pin-map.json')
    mismatches = [c for c in comparisons if not c['matches']]
    if mismatches:
        ctx.result['location'] = '; '.join(m['locator'] for m in mismatches)
        ctx.result['evidence'].remove(f'work/{ctx.cid}/pin-map.json')
        ctx.result['evidence'].extend(
            {'path': f'work/{ctx.cid}/pin-map.json', 'locator': m['locator']}
            for m in mismatches)
    ctx.result['details'].update(comparisons=comparisons, mismatches=mismatches, acquisitions=acquisitions)
    ctx.result['limitations'].append('Engineer-supplied applicability; only listed native pin names '
        'compared. Citation verification does not prove row/conditions selection or physical wiring.')
    ctx.result.update(execution='ok', outcome='defect' if mismatches else 'pass',
        summary=f'{len(mismatches)} native symbol pin mismatch(es) in {len(comparisons)} comparisons')


def describe_datasheet_facts(ctx):
    from .capabilities import text
    if ctx.inputs is None:
        raise ValueError('datasheet capability requires captured inputs')
    source = ctx.spec if ctx.kind == 'datasheet_facts' else ctx.prerequisite(
        ctx.params.get('facts_check'), ('datasheet_facts',))
    source_params = source['params']
    ctx.captured(source_params['facts'], immutable=True)
    validate_extractor(source_params['extractor'])
    facts = load_facts(ctx.inputs, source_params['facts'])
    obligations = load_obligations(ctx.inputs, source_params['facts'])
    if obligations is not None:
        ctx.result['acquisitions'] = obligations['acquired']
        for path in obligations['dependencies']:
            ctx.captured(path, immutable=True)
    for fact in facts:
        ctx.captured(fact['source']['path'], immutable=True)
    if ctx.kind == 'pin_map':
        ctx.prerequisite(ctx.params.get('netlist_check'), ('netlist',))
        symbols = ctx.params.get('symbols')
        if not isinstance(symbols, list) or not symbols:
            raise ValueError('pin_map requires explicit symbol applicability')
        by_id = {f['id']: f for f in facts}
        refs = set()
        for symbol in symbols:
            if not isinstance(symbol, dict) or set(symbol) != {'reference', 'part_number', 'facts'}:
                raise ValueError('symbol needs reference, part_number and facts')
            ref = text(symbol['reference'], 'reference')
            part = text(symbol['part_number'], 'part_number')
            if ref in refs:
                raise ValueError('duplicate symbol applicability')
            refs.add(ref)
            selected = symbol['facts']
            if not isinstance(selected, list) or not selected:
                raise ValueError('symbol requires pin facts')
            pins = set()
            for fid in selected:
                fact = by_id[text(fid, 'fact id')]
                if fact['kind'] != 'pin_function' or fact['part_number'] != part:
                    raise ValueError('pin fact is inapplicable')
                if fact['pin'] in pins:
                    raise ValueError('duplicate pin fact')
                pins.add(fact['pin'])
                ctx.result['predicates'].append(f"pin-map:{ref}:{fact['pin']}")


if __name__ != '__main__':
    _register_check('datasheet_facts', describe_datasheet_facts, check_datasheet_facts,
        early_prerequisites=(_requires.staged_placement_clear,), prerequisites=(_requires.stage_progress,))
if __name__ != '__main__':
    _register_check('pin_map', describe_datasheet_facts, check_pin_map,
        early_prerequisites=(_requires.staged_placement_clear,), prerequisites=(_requires.stage_progress,))
