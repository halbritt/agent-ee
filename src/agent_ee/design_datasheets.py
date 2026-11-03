"""Datasheet acquisition for design capture: ordered sources per part, each one recorded.

Host-side and network-bound. Only ``design_capture`` imports this module, inside
capture, so the offline readers in ``design_capture`` never load the datasheet
source cascade. Capture needs one complete PDF per purchased part (LCSC id). It
tries these sources in order until one gives that PDF:

1. ``declared``: the source in ``parts.<ref>.datasheet``. A part that declares a
   source uses only that source. A declared ``sha256`` must equal the PDF's.
2. ``jlc_file``: JLC's hosted file (``dataManualFileAccessIdUrl``).
3. ``manufacturer``: the manufacturer link in the same JLC row
   (``dataManualOfficialLink``).
4. ``cascade``: the datasheet source cascade over the captured JLC row. Its JLC
   tier reads ``dataManualUrl``. Design capture has no DigiKey capture or catalog
   index to declare, and it does not run Exa search.

Each source keeps its acquisition under ``datasheet-attempts/``. The selected PDF
of each part is copied into ``datasheets/``. ``datasheet-sources.json`` records
every source tried, with its outcome, failure class and byte counts. When a part
has no complete PDF, capture refuses with one message for each such part. The
message names the sources, why each failed and the next action.
"""
import hashlib
import json
import urllib.parse

from .datasheet_capture import capture_datasheets
from .design import DesignError
from .storage import canonical, identity, write_json

SCHEMA = 'agent-ee.design-datasheet-sources/1'
REPORT = 'datasheet-sources.json'
ATTEMPTS = 'datasheet-attempts'
# source -> (JLC row field, directory under ATTEMPTS, words used in messages)
SOURCES = {
    'declared': (None, 'declared', 'the declared source'),
    'jlc_file': ('dataManualFileAccessIdUrl', 'signed', "JLC's hosted file"),
    'manufacturer': ('dataManualOfficialLink', 'official', 'the manufacturer link in the JLC row'),
    'cascade': ('dataManualUrl', 'cascade-<n>', 'the datasheet cascade'),
}


def acquire(design, out, catalog):
    """Capture one complete datasheet for each LCSC id in ``catalog`` into ``out/datasheets``.

    Raises DesignError, after writing ``datasheet-sources.json``, when a part has none.
    """
    rows = {lcsc: _row(out, lcsc) for lcsc in sorted(catalog['parts'])}
    refs = {lcsc: sorted(r for r, p in design['parts'].items() if p['lcsc'] == lcsc) for lcsc in rows}
    declared = {lcsc: next((design['parts'][r]['datasheet'] for r in refs[lcsc]
                            if 'datasheet' in design['parts'][r]), None) for lcsc in rows}
    attempts, chosen = {lcsc: [] for lcsc in rows}, {}
    (out / ATTEMPTS).mkdir()
    _fetch_source(out, 'declared', {k: v['url'] for k, v in declared.items() if v}, attempts, chosen, declared)
    supplier = [lcsc for lcsc in rows if declared[lcsc] is None]
    for source in ('jlc_file', 'manufacturer'):
        field = SOURCES[source][0]
        _fetch_source(out, source, {k: rows[k].get(field) for k in supplier if k not in chosen},
                      attempts, chosen, declared)
    _cascade(out, [lcsc for lcsc in supplier if lcsc not in chosen], attempts, chosen)
    write_json(out / REPORT, _report(refs, declared, attempts, chosen))
    unresolved = sorted((lcsc for lcsc in rows if lcsc not in chosen), key=lambda lcsc: refs[lcsc][0])
    if unresolved:
        raise DesignError([_refusal(lcsc, refs[lcsc], declared[lcsc], attempts[lcsc]) for lcsc in unresolved])
    _publish(out, chosen)


def _row(out, lcsc):
    raw = json.loads((out / f'stock/responses/{lcsc}.json').read_bytes())
    return next(r for r in raw['data']['componentPageInfo']['list'] if r['componentCode'] == lcsc)


def _entry(source, field, record, detail, acquisition):
    return {'source': source, 'field': field, 'url': record['url'],
            'outcome': 'failed' if record['missing'] else 'selected',
            'failure': detail['failure'], 'transient': detail['transient'], 'reason': record['reason'],
            'http_status': detail['http_status'], 'content_length': detail['content_length'],
            'size_bytes': detail['size_bytes'], 'sha256': record['sha256'],
            'acquisition': acquisition, 'id': record['id']}


def _unavailable(source, field, reason):
    return {'source': source, 'field': field, 'url': None, 'outcome': 'unavailable',
            'failure': 'no_url', 'transient': False, 'reason': reason, 'http_status': None,
            'content_length': None, 'size_bytes': None, 'sha256': None, 'acquisition': None, 'id': None}


def _fetch_source(out, source, urls, attempts, chosen, declared):
    """Fetch one source for the parts in ``urls`` (LCSC id to URL or nothing); record each outcome."""
    field, directory, _ = SOURCES[source]
    bindings = []
    for lcsc, url in urls.items():
        if isinstance(url, str) and url.strip():
            bindings.append({'id': lcsc, 'datasheet_url': url.strip()})
        else:
            attempts[lcsc].append(_unavailable(source, field, 'the JLC row gives none'))
    if not bindings:
        return
    rel, details = f'{ATTEMPTS}/{directory}', {}
    manifest = capture_datasheets(bindings, out / rel, details=details)
    for record in manifest['parts']:
        entry = _entry(source, field, record, details[record['url']], f'{rel}/manifest.json')
        expected = declared[record['id']].get('sha256') if source == 'declared' else None
        if entry['outcome'] == 'selected' and expected is not None and record['sha256'] != expected:
            entry.update(outcome='failed', failure='hash_mismatch', transient=False,
                         reason=f'the PDF has sha256 {record["sha256"]}, not the declared {expected}')
        attempts[record['id']].append(entry)
        if entry['outcome'] == 'selected':
            chosen[record['id']] = (out / rel, record, entry)


def _cascade(out, pending, attempts, chosen):
    """Run the datasheet source cascade over the captured JLC rows of the ``pending`` parts."""
    from .datasheet_cascade import MAX_PARTS, REQUEST_SCHEMA, resolve_datasheets

    root = out.resolve()
    for number, start in enumerate(range(0, len(pending), MAX_PARTS), 1):
        batch = pending[start:start + MAX_PARTS]
        rel, details = f'{ATTEMPTS}/cascade-{number}', {}
        request = {'schema': REQUEST_SCHEMA, 'prior_selection': None, 'parts': [
            {'key': lcsc, 'digikey': None, 'jlc': {'capture': 'stock/catalog.json', 'lcsc': lcsc},
             'catalog': None, 'exa': None} for lcsc in batch]}
        report = resolve_datasheets(root, request, rel, details=details)
        records = {}
        if report['acquisition'] is not None:
            manifest = json.loads((root / report['acquisition']['path']).read_bytes())
            records = {r['id']: r for r in manifest['parts']}
        for lcsc in batch:
            part = report['parts'][lcsc]
            fields = {t['tier']: (t['source'] or {}).get('field') for t in part['tiers']}
            for tier in part['tiers']:
                if tier['status'] == 'unavailable':
                    attempts[lcsc].append(_unavailable('cascade', fields[tier['tier']], tier['reason']))
            for attempt in part['attempts']:
                if attempt['outcome'] == 'not_attempted':
                    continue
                record = records[attempt['id']]
                entry = _entry('cascade', fields[attempt['tier']], record, details[record['url']],
                               report['acquisition']['path'])
                # Without a prior selection the cascade only selects or fails a candidate.
                entry['outcome'] = 'selected' if attempt['outcome'] == 'selected' else 'failed'
                attempts[lcsc].append(entry)
                if entry['outcome'] == 'selected':
                    chosen[lcsc] = (root / rel / 'datasheets', record, entry)


def _report(refs, declared, attempts, chosen):
    parts = {}
    for lcsc in sorted(attempts):
        entry = chosen[lcsc][2] if lcsc in chosen else None
        parts[lcsc] = {'references': refs[lcsc], 'declared': declared[lcsc],
                       'outcome': 'selected' if entry else 'unresolved',
                       'selected': None if entry is None else
                       {k: entry[k] for k in ('source', 'url', 'sha256', 'size_bytes')},
                       'attempts': attempts[lcsc]}
    return {'schema': SCHEMA, 'parts': parts}


def _publish(out, chosen):
    """``datasheets/``: each part's selected PDF and its record, under the part's LCSC id."""
    target = out / 'datasheets'
    target.mkdir()
    records = []
    for lcsc in sorted(chosen):
        directory, record, _ = chosen[lcsc]
        raw = (directory / record['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError(f'{directory / record["path"]}: retained datasheet bytes changed during capture')
        name = record['sha256'] + '.pdf'
        if not (target / name).exists():
            (target / name).write_bytes(raw)
        records.append({**record, 'id': lcsc, 'path': name})
    bindings = [{'id': r['id'], 'datasheet_url': r['url']} for r in records]
    manifest = {'schema': 'agent-ee.datasheet-capture/1', 'bindings_sha256': identity(bindings),
                'outcome': 'captured', 'parts': records}
    (target / 'manifest.json').write_bytes(canonical(manifest) + b'\n')


def _short(url):
    """The URL without its query, which for a signed link is long and expires."""
    parts = urllib.parse.urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}{parts.path}' + ('?...' if parts.query else '')


def _describe(attempt, field):
    words, name = SOURCES[attempt['source']][2], attempt['field'] or field
    if attempt['outcome'] == 'unavailable':
        return f'{words} ({name}): {attempt["reason"]}'
    kind = 'can be temporary' if attempt['transient'] else 'repeats on every download'
    return f'{words} ({name}) {_short(attempt["url"])}: {attempt["reason"]} [{kind}]'


def _refusal(lcsc, refs, declared, attempts):
    """One refusal line: the part, every source tried and why it failed, and the next action.

    The action says "retry" only when a failure can be temporary.
    """
    field = f'parts.{refs[0]}.datasheet'
    tried = '; '.join(f'({n}) {_describe(a, field)}' for n, a in enumerate(attempts, 1))
    mismatch = next((a for a in attempts if a['failure'] == 'hash_mismatch'), None)
    temporary = any(a['transient'] for a in attempts)
    if mismatch is not None:
        folder = mismatch['acquisition'].rsplit('/', 1)[0]
        action = (f'the URL serves a different document from the recorded version; inspect the copy kept in '
                  f'{folder}/, then set {field}.sha256 to its hash or point {field}.url at the recorded version')
    elif declared is not None and temporary:
        action = (f'retry design capture into a new directory, because this failure can be temporary; '
                  f'if it repeats, correct {field}.url')
    elif declared is not None:
        action = (f'this failure repeats on every download: correct {field}.url so that it gives a complete '
                  f'PDF, or remove {field} to use the JLC sources')
    elif temporary:
        action = (f'retry design capture into a new directory, because a failure marked "can be temporary" may '
                  f'pass; if the failures repeat, declare a complete copy in {field} as '
                  '{"url": ..., "sha256": ...} or choose another part')
    else:
        action = ('every failure repeats on each download, so a new capture fails in the same way; to keep this '
                  f'part, declare a complete copy of its datasheet in {field} as '
                  '{"url": "https://...", "sha256": "<64 lowercase hex digits>"}; otherwise choose another part')
    return (f'{field}: no complete datasheet PDF for {lcsc} ({", ".join(refs)}); sources tried: {tried}. '
            f'Next: {action}. {REPORT} in the capture directory lists every source with its byte counts')
