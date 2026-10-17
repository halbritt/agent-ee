"""Textual citation support and an explicitly scoped native symbol pin consumer."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .candidate import safe_relative
from .storage import digest, read_json, write_json

EXTRACTOR = '/usr/bin/pdftotext'


def load_facts(inputs, rel):
    path = inputs / str(safe_relative(rel))
    data = read_json(path)
    if (not isinstance(data, dict) or set(data) != {'schema', 'facts'} or
            data['schema'] != 'agent-ee.datasheet-facts/1' or
            not isinstance(data['facts'], list) or not data['facts']):
        raise ValueError('expected nonempty agent-ee.datasheet-facts/1')
    ids = set()
    for fact in data['facts']:
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
    return data['facts']


def value_in_quote(value, unit, quote):
    """Whole-token occurrence: CAN is not in CANL, 5 is not in 15, 4.5 or 5.5."""
    suffix = rf'(?:\s*{re.escape(unit)})?' if unit.strip() else ''
    return re.search(r'(?<!\w)(?<!\d[.,])' + re.escape(value) + suffix + r'(?!\w)(?![.,]\d)',
                     quote) is not None


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
            # Poppler separates physical PDF pages with form feeds, including a final delimiter.
            pages = (work / 'page.txt').read_text(encoding='utf-8').split('\f')
            if pages and pages[-1] == '':
                pages.pop()
            if source['page'] > len(pages):
                record['reason'] = 'page_out_of_range'
            elif not pages[source['page'] - 1].strip():
                record.update(status='unsupported', reason='page_has_no_text')
            elif source['quote'] not in pages[source['page'] - 1]:
                record['reason'] = 'quote_absent_from_page'
            else:
                record.update(status='verified', reason='exact_quote_and_value_supported')
    try:
        verify_extractor(tool)
    except (ValueError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    write_json(ctx.work / 'facts.json', {'facts': records, 'extractor': tool})
    ctx.evidence('facts.json')
    ctx.result['details'].update(facts=records, extractor=tool)
    ctx.result['limitations'].append('Citation verification proves text occurrence only; it does not '
        'establish part applicability, correct row, conditions, units or engineering interpretation.')
    verified = all(r['status'] == 'verified' for r in records)
    ctx.result.update(execution='ok' if verified else 'unsupported',
                      outcome='pass' if verified else 'unknown',
                      summary='All datasheet citations verified' if verified else 'Unverified datasheet citations')


def prerequisite(ctx, cid, kind, artifact):
    from .checks import CheckError, SAFE_ID
    if not isinstance(cid, str) or not SAFE_ID.fullmatch(cid):
        raise CheckError('unsupported', f'{kind} prerequisite needs a safe check id')
    spec = next((s for s in ctx.scope['checks'] if s['id'] == cid), None)
    path = ctx.run_dir / 'checks' / f'{cid}.json'
    if not spec or spec['kind'] != kind or not path.is_file():
        raise CheckError('unsupported', f'{kind} prerequisite missing or not yet run')
    result = read_json(path)
    if result.get('candidate_id') != ctx.manifest['candidate_id'] or result.get('execution') != 'ok':
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
        records = read_json(facts_path)['facts']
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
    write_json(ctx.work / 'pin-map.json', {'comparisons': comparisons})
    ctx.evidence('pin-map.json')
    mismatches = [c for c in comparisons if not c['matches']]
    if mismatches:
        ctx.result['location'] = '; '.join(m['locator'] for m in mismatches)
        ctx.result['evidence'].remove(f'work/{ctx.cid}/pin-map.json')
        ctx.result['evidence'].extend(
            {'path': f'work/{ctx.cid}/pin-map.json', 'locator': m['locator']}
            for m in mismatches)
    ctx.result['details'].update(comparisons=comparisons, mismatches=mismatches)
    ctx.result['limitations'].append('Engineer-supplied applicability; only listed native pin names '
        'compared. Citation verification does not prove row/conditions selection or physical wiring.')
    ctx.result.update(execution='ok', outcome='defect' if mismatches else 'pass',
        summary=f'{len(mismatches)} native symbol pin mismatch(es) in {len(comparisons)} comparisons')
