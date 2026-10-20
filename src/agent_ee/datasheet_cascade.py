"""Host-side cascading datasheet URL resolution and source-selection producer.

Stage 2 preparation only. For each declared part key the producer walks an
ordered cascade of candidate datasheet URLs:

1. ``digikey``: ``primary_datasheet_url`` of a found part in a replayed
   ``agent-ee.digikey-capture/1`` (AGENTEE-246);
2. ``jlc``: ``dataManualUrl`` of the exact row in a replayed
   ``agent-ee.jlc-catalog/1`` capture, then ``catalog``: the raw datasheet URL
   of the pinned ``agent-ee.catalog-identity/1`` row (AGENTEE-247);
3. ``exa``: result URLs of one local ``exa-search`` query, only with the owner
   opt-in ``allow_network=True`` / ``--allow-network``. The CLI runs in an owned,
   deadline-bounded child process; its raw JSON stdout is retained byte for byte
   with its SHA-256 and UTC ``fetched_at``.

Candidates are fetched one at a time through ``datasheet_capture``. A candidate
that fails the ``%PDF-``/``%%EOF`` envelope or matches a rejected hash of a prior
``agent-ee.datasheet-source-selection/1`` decision advances the cascade; the
first remaining PDF is selected. One new output directory is published by a
single rename: ``datasheets/manifest.json`` (``agent-ee.datasheet-capture/1``,
every attempted candidate), ``search/``, ``source-selection.json`` (selected,
rejected and failed sources) and ``cascade.json`` (``agent-ee.datasheet-cascade/1``,
every tier and candidate with its provenance). A part with no acceptable PDF
keeps an empty selection, which every consumer refuses: unknown never passes.

``validate_cascade`` replays all of it offline. Checks never run this producer
and no check or offline module imports it; Exa never runs in a check sandbox.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import time
import urllib.parse

from .catalog import exact_fields, nonempty, publish_directory, strict_json
from .storage import canonical, identity

REQUEST_SCHEMA = 'agent-ee.datasheet-cascade-request/1'
SCHEMA = 'agent-ee.datasheet-cascade/1'
SELECTION_SCHEMA = 'agent-ee.datasheet-source-selection/1'
TIERS = ('digikey', 'jlc', 'catalog', 'exa')
PART_KEYS = ('key', 'digikey', 'jlc', 'catalog', 'exa')
MAX_PARTS = 64
MAX_QUERY_CHARS = 250
MAX_RESULTS = 10
MAX_SEARCH_BYTES = 1024 * 1024
MAX_SEARCH_SECONDS = 120
DEFAULT_INTERVAL = 1.0
MIN_INTERVAL = 0.5
SELECTION = 'source-selection.json'
ACQUISITION = 'datasheets/manifest.json'
REPORT = 'cascade.json'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _rel(value):
    from .parts_manifest import _path
    return _path(value)


def _domain(value):
    nonempty(value)
    if (value != value.lower() or len(value) > 253 or '/' in value or ':' in value or
            not all(label and len(label) <= 63 and label.replace('-', '').isalnum() for label in value.split('.'))):
        raise ValueError('include_domains entries must be lowercase host names')
    return value


def validate_request(request):
    """Strict declared cascade inputs; every tier key is present, null when undeclared."""
    from .parts_manifest import _sha
    exact_fields(request, ('schema', 'prior_selection', 'parts'))
    if request['schema'] != REQUEST_SCHEMA:
        raise ValueError('expected ' + REQUEST_SCHEMA)
    if request['prior_selection'] is not None:
        exact_fields(request['prior_selection'], ('path', 'sha256'))
        _rel(request['prior_selection']['path'])
        _sha(request['prior_selection']['sha256'])
    parts = request['parts']
    if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS:
        raise ValueError(f'expected 1..{MAX_PARTS} cascade parts')
    keys = set()
    for part in parts:
        exact_fields(part, PART_KEYS)
        nonempty(part['key'])
        if part['key'] in keys or ']' in part['key'] or '#' in part['key']:
            raise ValueError('cascade part keys must be unique and contain no "]" or "#"')
        keys.add(part['key'])
        if part['digikey'] is not None:
            exact_fields(part['digikey'], ('capture', 'key'))
            _rel(part['digikey']['capture'])
            nonempty(part['digikey']['key'])
        if part['jlc'] is not None:
            exact_fields(part['jlc'], ('capture', 'lcsc'))
            _rel(part['jlc']['capture'])
            nonempty(part['jlc']['lcsc'])
        if part['catalog'] is not None:
            exact_fields(part['catalog'], ('directory', 'manifest_sha256', 'lcsc'))
            _rel(part['catalog']['directory'])
            _sha(part['catalog']['manifest_sha256'])
            nonempty(part['catalog']['lcsc'])
        if part['exa'] is not None:
            exact_fields(part['exa'], ('query', 'include_domains', 'max_results'))
            if len(nonempty(part['exa']['query'])) > MAX_QUERY_CHARS:
                raise ValueError(f'Exa query exceeds {MAX_QUERY_CHARS} characters')
            domains = part['exa']['include_domains']
            if not isinstance(domains, list) or len(set(map(_domain, domains))) != len(domains):
                raise ValueError('include_domains must be a list of distinct host names')
            if type(part['exa']['max_results']) is not int or not 1 <= part['exa']['max_results'] <= MAX_RESULTS:
                raise ValueError(f'Exa max_results must be 1..{MAX_RESULTS}')
    return request


def _prior_rejections(root, request):
    """Rejected hashes per key from an existing selection decision; never its selections."""
    from .datasheet_selection import validate_document
    from .parts_manifest import _read_blob
    ref = request['prior_selection']
    if ref is None:
        return {}, {}
    document = validate_document(strict_json(_read_blob(root, ref)), context='prior selection')
    return {k: list(e['rejected']) for k, e in document['parts'].items()}, {ref['path']: ref['sha256']}


# ------------------------------------------------------------- offline tiers 1-2

def _unavailable(tier, source, reason):
    return {'tier': tier, 'status': 'unavailable', 'source': source, 'urls': [], 'reason': reason}


def _digikey_tier(root, declared, dependencies):
    from .digikey_capture import load_capture
    # A failed DigiKey request is a recorded unavailable tier, never a silent skip.
    doc, _, read = load_capture(root, declared['capture'], allow_failed=True)
    dependencies.update(read)
    key = declared['key']
    record = next((r for r in doc['requests'] if r['key'] == key), None)
    if record is None:
        raise ValueError(f'DigiKey capture has no request keyed {key!r}')
    source = {'capture': {'path': declared['capture'], 'sha256': read[declared['capture']]},
              'response': None, 'key': key, 'field': 'DatasheetUrl', 'fetched_at': record['fetched_at']}
    if record['response'] is not None:
        path = (PurePosixPath(declared['capture']).parent / record['response']['path']).as_posix()
        source['response'] = {'path': path, 'sha256': record['response']['sha256']}
    if key not in doc['parts']:
        return _unavailable('digikey', source, f'DigiKey request {record["status"]}: {record["reason"]}')
    url = doc['parts'][key]['primary_datasheet_url']
    if url is None:
        return _unavailable('digikey', source, 'DigiKey record has no primary datasheet URL')
    return {'tier': 'digikey', 'status': 'candidates', 'source': source, 'urls': [url], 'reason': None}


def _jlc_tier(root, declared, dependencies):
    from .jlc_stock_capture import response_datasheet_url, validate_capture
    from .parts_manifest import read_captured
    raw = read_captured(root, declared['capture'])
    dependencies[declared['capture']] = hashlib.sha256(raw).hexdigest()
    doc = strict_json(raw)
    parent = PurePosixPath(declared['capture']).parent
    responses = {}

    def read_response(rel):
        path = (parent / rel).as_posix()
        data = read_captured(root, path)
        dependencies[path] = hashlib.sha256(data).hexdigest()
        responses[rel] = data
        return data
    validate_capture(doc, read_response)
    lcsc = declared['lcsc']
    record = next((r for r in doc['requests'] if r['lcsc'] == lcsc), None)
    if record is None:
        raise ValueError(f'JLCPCB capture has no request for {lcsc}')
    source = {'capture': {'path': declared['capture'], 'sha256': dependencies[declared['capture']]},
              'response': None, 'lcsc': lcsc, 'field': 'dataManualUrl', 'fetched_at': record['fetched_at']}
    if record['response'] is not None:
        source['response'] = {'path': (parent / record['response']['path']).as_posix(),
                              'sha256': record['response']['sha256']}
    if lcsc not in doc['parts']:
        return _unavailable('jlc', source, f'JLCPCB request {record["status"]}: {record["reason"]}')
    url = response_datasheet_url(responses[record['response']['path']], lcsc)
    if url is None:
        return _unavailable('jlc', source, 'JLCPCB row has no dataManualUrl')
    return {'tier': 'jlc', 'status': 'candidates', 'source': source, 'urls': [url], 'reason': None}


def _catalog_tier(root, declared, dependencies):
    from .catalog_identity import load_identity_index
    from .parts_manifest import read_captured
    directory = declared['directory']
    # Use the captured-path reader so links are refused before the index loads.
    read_captured(root, directory + '/manifest.json')
    with load_identity_index(Path(root) / directory, declared['manifest_sha256']) as index:
        database = index.manifest['database']
        dependencies[directory + '/manifest.json'] = declared['manifest_sha256']
        dependencies[directory + '/' + database['path']] = database['sha256']
        row = index.by_lcsc(declared['lcsc'])
        source = {'manifest_sha256': declared['manifest_sha256'], 'database_sha256': database['sha256'],
                  'directory': directory, 'lcsc': declared['lcsc'], 'field': 'datasheet_url',
                  'row': None if row is None else row['provenance']}
    if row is None:
        return _unavailable('catalog', source, 'LCSC id is absent from the identity index (unknown)')
    if row['datasheet_url'] is None:
        return _unavailable('catalog', source, 'identity row has no datasheet URL')
    return {'tier': 'catalog', 'status': 'candidates', 'source': source, 'urls': [row['datasheet_url']],
            'reason': None}


OFFLINE_TIERS = {'digikey': _digikey_tier, 'jlc': _jlc_tier, 'catalog': _catalog_tier}


def offline_tiers(root, part, dependencies):
    """Tiers 1-2 re-derived from captured bytes; no network, fetch or write."""
    return [{'tier': tier, 'status': 'undeclared', 'source': None, 'urls': [], 'reason': None}
            if part[tier] is None else OFFLINE_TIERS[tier](root, part[tier], dependencies)
            for tier in ('digikey', 'jlc', 'catalog')]


# ------------------------------------------------------------- tier 3: Exa

def parse_search(raw, include_domains, max_results):
    """Ordered result URLs from retained ``exa-search`` stdout; refuses unknown shapes."""
    document = strict_json(raw)
    if not isinstance(document, dict):
        raise ValueError('Exa search output must be a JSON object')
    if 'error' in document:
        raise ValueError(f'Exa search reported an error: {document["error"]}')
    results = document.get('results')
    if not isinstance(results, list):
        raise ValueError('Exa search output has no results list')
    urls, filtered = [], []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get('url'), str) or not result['url']:
            raise ValueError('Exa result requires a URL')
        url = result['url']
        host = (urllib.parse.urlsplit(url).hostname or '').lower()
        if include_domains and not any(host == d or host.endswith('.' + d) for d in include_domains):
            filtered.append(url)
        elif url not in urls and len(urls) < max_results:
            urls.append(url)
    return urls, filtered


def search_argv(command, exa):
    argv = [*command, '--max-results', str(exa['max_results'])]
    if exa['include_domains']:
        argv += ['--include-domains', ','.join(exa['include_domains'])]
    return argv + ['--', exa['query']]


def _search(command, exa, timeout, stage, index):
    from .datasheet_capture import run_owned_child
    argv = search_argv(command, exa)
    record = {'index': index, 'query': exa['query'], 'include_domains': exa['include_domains'],
              'max_results': exa['max_results'], 'argv': argv, 'fetched_at': _now(), 'status': 'failed',
              'response': None, 'reason': None}
    try:
        stdout = run_owned_child(argv, timeout)
    except (OSError, RuntimeError) as exc:
        record['reason'] = str(exc).strip()[:500]
        return record, []
    if stdout is None:
        record['reason'] = 'search deadline exceeded'
        return record, []
    if len(stdout) > MAX_SEARCH_BYTES:
        record['reason'] = 'search output byte limit exceeded; bytes not retained'
        return record, []
    rel = f'search/{index}.json'
    with (stage / rel).open('xb') as stream:
        stream.write(stdout)
    record['response'] = {'path': rel, 'sha256': hashlib.sha256(stdout).hexdigest(), 'size_bytes': len(stdout)}
    try:
        urls, _ = parse_search(stdout, exa['include_domains'], exa['max_results'])
    except ValueError as exc:
        record['reason'] = 'unrecognised search output: ' + str(exc)[:500]
        return record, []
    record['status'] = 'ok'
    return record, urls


# ------------------------------------------------------------- producer (host only)

def _limits(timeout, max_bytes, search_timeout, interval):
    from .datasheet_capture import _limits as fetch_limits
    fetch_limits(timeout, max_bytes)
    for value, low, high, label in ((search_timeout, 0, MAX_SEARCH_SECONDS, 'search timeout'),
                                    (interval, MIN_INTERVAL, 3600, 'search interval')):
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or value < low or value > high or value == 0):
            raise ValueError(f'{label} must be finite and within [{low}, {high}], nonzero')


def _attempt_outcome(record, rejected):
    if record['missing']:
        return 'failed', record['reason']
    match = next((r for r in rejected if r['sha256'] == record['sha256']), None)
    if match is not None:
        return 'rejected', f'matches rejected source {match["id"]}: {match["reason"]}'
    return 'selected', None


def resolve_datasheets(root, request, out, *, allow_network=False, exa_command=None, timeout=30,
                       max_bytes=None, search_timeout=60, min_interval=DEFAULT_INTERVAL):
    """Walk the cascade and publish one new output directory ``out`` (relative to ``root``).

    Without ``allow_network=True`` the Exa tier is recorded as not run. Failed
    URLs, failed searches and rejected PDFs are recorded; capture or write
    infrastructure failures raise and publish nothing. Never call this from a
    check or campaign writer.
    """
    from .datasheet_capture import MAX_BYTES, _capture_parts
    from .datasheet_selection import validate_document
    root = Path(root)
    max_bytes = MAX_BYTES if max_bytes is None else max_bytes
    validate_request(request)
    _limits(timeout, max_bytes, search_timeout, min_interval)
    out_rel = _rel(out)
    destination = root / out_rel
    if destination.parent.resolve(strict=True) != destination.parent.absolute():
        raise ValueError('output parent must be real and canonical')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if allow_network is True:
        if not isinstance(exa_command, (list, tuple)) or not exa_command or any(
                not isinstance(a, str) or not a for a in exa_command):
            raise ValueError('Exa tier needs the exa-search command argv')
    elif allow_network is not False:
        raise ValueError('allow_network must be a boolean')
    rejected_by_key, dependencies = _prior_rejections(root, request)
    tiers_by_key = {p['key']: offline_tiers(root, p, dependencies) for p in request['parts']}
    stage = Path(tempfile.mkdtemp(prefix='.datasheet-cascade-', dir=destination.parent))
    published = False
    try:
        (stage / 'datasheets').mkdir()
        (stage / 'search').mkdir()
        fetched, bindings, records, searches, parts = {}, [], [], [], {}
        last_search = None
        for part in request['parts']:
            key, rejected = part['key'], rejected_by_key.get(part['key'], [])
            tiers, attempts, selected = tiers_by_key[key], [], None

            def attempt(tier, url, rank):
                acquisition_id = _attempt_id(key, tier, rank)
                binding = {'id': acquisition_id, 'datasheet_url': url}
                record = _capture_parts([binding], stage / 'datasheets', timeout, max_bytes, fetched)[0]
                bindings.append(binding)
                records.append(record)
                outcome, reason = _attempt_outcome(record, rejected)
                attempts.append({'id': acquisition_id, 'tier': tier, 'url': url, 'outcome': outcome,
                                 'sha256': record['sha256'], 'reason': reason})
                return attempts[-1] if outcome == 'selected' else None

            for tier in tiers:
                for url in tier['urls']:
                    if selected is None:
                        selected = attempt(tier['tier'], url, None)
                    else:
                        attempts.append(_skipped(key, tier['tier'], url, None))
            search, urls = None, []
            if part['exa'] is not None and selected is None and allow_network:
                if last_search is not None:
                    time.sleep(max(0.0, min_interval - (time.monotonic() - last_search)))
                search, urls = _search(exa_command, part['exa'], search_timeout, stage, len(searches))
                last_search = time.monotonic()
                searches.append({'key': key, **search})
            exa = _exa_tier(part, selected is not None, allow_network, search, urls)
            for rank, url in enumerate(urls, 1):
                if selected is None:
                    selected = attempt('exa', url, rank)
                else:
                    attempts.append(_skipped(key, 'exa', url, rank))
            parts[key] = {'outcome': 'selected' if selected else 'unresolved',
                          'tiers': tiers + [exa], 'attempts': attempts}
        bindings_sorted = sorted(bindings, key=lambda b: b['id'])
        records.sort(key=lambda r: r['id'])
        manifest = {'schema': 'agent-ee.datasheet-capture/1', 'bindings_sha256': identity(bindings_sorted),
                    'outcome': 'missing' if any(r['missing'] for r in records) else 'captured',
                    'parts': records} if records else None
        acquisition = None
        if manifest is not None:
            payload = canonical(manifest) + b'\n'
            with (stage / ACQUISITION).open('xb') as stream:
                stream.write(payload)
            acquisition = {'path': f'{out_rel}/{ACQUISITION}', 'sha256': hashlib.sha256(payload).hexdigest()}
        selection = {'schema': SELECTION_SCHEMA, 'parts': {}}
        for key, report in parts.items():
            selection['parts'][key] = _selection_entry(report['attempts'], rejected_by_key.get(key, []),
                                                       acquisition)
        validate_document(selection, context='cascade selection')
        selection_bytes = canonical(selection) + b'\n'
        with (stage / SELECTION).open('xb') as stream:
            stream.write(selection_bytes)
        report = {'schema': SCHEMA, 'request': request, 'request_sha256': identity(request),
                  'completed_at': _now(), 'allow_network': allow_network is True,
                  'acquisition': acquisition, 'searches': searches,
                  'selection': {'path': f'{out_rel}/{SELECTION}',
                                'sha256': hashlib.sha256(selection_bytes).hexdigest()},
                  'dependencies': dict(sorted(dependencies.items())), 'parts': parts}
        with (stage / REPORT).open('xb') as stream:
            stream.write(canonical(report) + b'\n')
        publish_directory(stage, destination)
        published = True
        return report
    finally:
        if not published:
            shutil.rmtree(stage)


def _attempt_id(key, tier, rank):
    return f'{key}#{tier}' + ('' if rank is None else f'-{rank}')


def _skipped(key, tier, url, rank):
    return {'id': _attempt_id(key, tier, rank), 'tier': tier, 'url': url, 'outcome': 'not_attempted',
            'sha256': None, 'reason': 'an earlier candidate was selected'}


def _exa_tier(part, selected, allow_network, search, urls):
    """The Exa tier entry; a declared but unrun or failed search stays visible."""
    exa = {'tier': 'exa', 'status': 'undeclared', 'source': None, 'urls': list(urls), 'reason': None}
    if part['exa'] is None:
        return exa
    if selected:
        return {**exa, 'status': 'not_reached', 'reason': 'an earlier tier was selected'}
    if not allow_network:
        return {**exa, 'status': 'unavailable', 'reason': 'Exa search not run: network not enabled (--allow-network)'}
    source = {'search': search['index'], 'response': search['response'], 'fetched_at': search['fetched_at']}
    if search['status'] != 'ok':
        return {**exa, 'status': 'unavailable', 'source': source, 'reason': search['reason']}
    return {**exa, 'status': 'candidates' if urls else 'unavailable', 'source': source,
            'reason': None if urls else 'no acceptable search result URL'}


def _selection_entry(attempts, prior_rejected, acquisition):
    entry = {'selected': [], 'rejected': list(prior_rejected), 'failed': []}
    for attempt in attempts:
        if attempt['outcome'] == 'not_attempted':
            continue
        provenance = {**acquisition, 'locator': f'parts[id={attempt["id"]}]'}
        if attempt['outcome'] == 'selected':
            entry['selected'].append({'id': attempt['id'], 'sha256': attempt['sha256'], 'provenance': provenance})
        elif attempt['outcome'] == 'rejected':
            entry['rejected'].append({'id': attempt['id'], 'sha256': attempt['sha256'], 'reason': attempt['reason']})
        else:
            entry['failed'].append({'id': attempt['id'], 'reason': f'{attempt["url"]}: {attempt["reason"]}',
                                    'provenance': provenance})
    if len({s['id'] for s in entry['rejected']}) != len(entry['rejected']):
        raise ValueError('cascade acquisition id collides with a prior rejected source id')
    return entry


# ------------------------------------------------------------- offline replay

def validate_cascade(root, path):
    """Replay a published cascade without network; return its dependency map.

    Tiers 1-2 are re-derived from their captures, Exa URLs from the retained
    search bytes, every attempt from the acquisition, and the source selection
    from the attempts in cascade order.
    """
    from .datasheet_facts import load_acquisition
    from .datasheet_selection import validate_document
    from .parts_manifest import _read_blob, read_captured
    root = Path(root)
    raw = read_captured(root, path)
    report = strict_json(raw)
    exact_fields(report, ('schema', 'request', 'request_sha256', 'completed_at', 'allow_network', 'acquisition',
                          'searches', 'selection', 'dependencies', 'parts'))
    if report['schema'] != SCHEMA or report['request_sha256'] != identity(validate_request(report['request'])):
        raise ValueError('unsupported cascade report or request identity mismatch')
    request, base = report['request'], PurePosixPath(path).parent
    if PurePosixPath(path).name != REPORT or report['selection']['path'] != f'{base}/{SELECTION}':
        raise ValueError('cascade report and selection must share one published directory')
    rejected_by_key, dependencies = _prior_rejections(root, request)
    tiers_by_key = {p['key']: offline_tiers(root, p, dependencies) for p in request['parts']}
    if canonical(dependencies) != canonical(report['dependencies']):
        raise ValueError('cascade input dependencies differ from the captured sources')
    dependencies[path] = hashlib.sha256(raw).hexdigest()
    selection = validate_document(strict_json(_read_blob(root, report['selection'])), context='cascade selection')
    dependencies[report['selection']['path']] = report['selection']['sha256']
    acquired = {}
    if report['acquisition'] is not None:
        if report['acquisition']['path'] != f'{base}/{ACQUISITION}':
            raise ValueError('cascade acquisition must be in its published directory')
        manifest = strict_json(_read_blob(root, report['acquisition']))
        if not isinstance(manifest, dict) or manifest.get('schema') != 'agent-ee.datasheet-capture/1':
            raise ValueError('live cascade requires agent-ee.datasheet-capture/1')
        bindings = sorted([{'id': r['id'], 'datasheet_url': r['url']} for r in manifest['parts']],
                          key=lambda b: b['id'])
        acquired, paths = load_acquisition(root, report['acquisition'], bindings)
        for rel in paths:
            dependencies[rel] = hashlib.sha256(read_captured(root, rel)).hexdigest()
    searches = report['searches']
    if not isinstance(searches, list) or [s.get('index') for s in searches] != list(range(len(searches))):
        raise ValueError('cascade searches must be indexed in order')
    if searches and not report['allow_network']:
        raise ValueError('Exa search recorded without the network opt-in')
    exa_urls, exa_searches = {}, {}
    for search in searches:
        exact_fields(search, ('key', 'index', 'query', 'include_domains', 'max_results', 'argv', 'fetched_at',
                              'status', 'response', 'reason'))
        declared = next((p['exa'] for p in request['parts'] if p['key'] == search['key']), None)
        if declared is None or search['key'] in exa_urls or any(
                search[k] != declared[k] for k in ('query', 'include_domains', 'max_results')):
            raise ValueError('cascade search differs from its declared query')
        nonempty(search['fetched_at'])
        if not search['fetched_at'].endswith('+00:00'):
            raise ValueError('search fetched_at must be UTC')
        urls = []
        if search['response'] is not None:
            exact_fields(search['response'], ('path', 'sha256', 'size_bytes'))
            if search['response']['path'] != f'search/{search["index"]}.json':
                raise ValueError('search response path mismatch')
            rel = f'{base}/{search["response"]["path"]}'
            data = read_captured(root, rel)
            if (hashlib.sha256(data).hexdigest() != search['response']['sha256'] or
                    len(data) != search['response']['size_bytes']):
                raise ValueError('retained search response size/sha256 mismatch')
            dependencies[rel] = search['response']['sha256']
            try:
                urls = parse_search(data, declared['include_domains'], declared['max_results'])[0]
                ok = True
            except ValueError:
                ok = False
            if ok != (search['status'] == 'ok'):
                raise ValueError('recorded search status contradicts retained response')
        elif search['status'] != 'failed':
            raise ValueError('a search without retained response must be failed')
        if search['status'] == 'failed':
            nonempty(search['reason'])
        exa_urls[search['key']] = urls
        exa_searches[search['key']] = search
    if set(report['parts']) != {p['key'] for p in request['parts']} or set(selection['parts']) != set(report['parts']):
        raise ValueError('cascade parts differ from the request')
    seen = set()
    for part in request['parts']:
        key, entry = part['key'], report['parts'][part['key']]
        exact_fields(entry, ('outcome', 'tiers', 'attempts'))
        if canonical(entry['tiers'][:3]) != canonical(tiers_by_key[key]):
            raise ValueError(f'{key}: recorded tiers 1-2 differ from captured sources')
        expected_urls = exa_urls.get(key, [])
        ordered = ([(t['tier'], u, None) for t in tiers_by_key[key] for u in t['urls']] +
                   [('exa', u, rank) for rank, u in enumerate(expected_urls, 1)])
        if [(a.get('tier'), a.get('url'), a.get('id')) for a in entry['attempts']] != [
                (tier, url, _attempt_id(key, tier, rank)) for tier, url, rank in ordered]:
            raise ValueError(f'{key}: attempts differ from cascade order')
        selected = None
        for attempt in entry['attempts']:
            exact_fields(attempt, ('id', 'tier', 'url', 'outcome', 'sha256', 'reason'))
            if attempt['outcome'] == 'not_attempted':
                if selected is None or attempt != _skipped(key, attempt['tier'], attempt['url'],
                                                           None if attempt['tier'] != 'exa' else
                                                           int(attempt['id'].rsplit('-', 1)[1])):
                    raise ValueError(f'{key}: a candidate was skipped before any selection')
                continue
            if selected is not None:
                raise ValueError(f'{key}: a candidate was attempted after selection')
            record = acquired.get(attempt['id'])
            if record is None or attempt['id'] in seen:
                raise ValueError(f'{key}: attempt {attempt["id"]} has no unique acquisition record')
            seen.add(attempt['id'])
            outcome, reason = _attempt_outcome(record, rejected_by_key.get(key, []))
            if (outcome, reason, record['sha256'], record['url']) != (
                    attempt['outcome'], attempt['reason'], attempt['sha256'], attempt['url']):
                raise ValueError(f'{key}: attempt {attempt["id"]} differs from its acquisition')
            if outcome == 'selected':
                selected = attempt
        before_exa = any(a['outcome'] == 'selected' for a in entry['attempts'] if a['tier'] != 'exa')
        search = exa_searches.get(key)
        searched = part['exa'] is not None and not before_exa and report['allow_network']
        if searched != (search is not None):
            raise ValueError(f'{key}: Exa search must run exactly when the cascade reaches it with the opt-in')
        if len(entry['tiers']) != 4 or canonical(entry['tiers'][3]) != canonical(
                _exa_tier(part, before_exa, report['allow_network'], search, expected_urls)):
            raise ValueError(f'{key}: recorded Exa tier differs from the retained search')
        if entry['outcome'] != ('selected' if selected else 'unresolved'):
            raise ValueError(f'{key}: recorded outcome contradicts attempts')
        if canonical(selection['parts'][key]) != canonical(
                _selection_entry(entry['attempts'], rejected_by_key.get(key, []), report['acquisition'])):
            raise ValueError(f'{key}: source selection differs from the replayed cascade')
    if seen != set(acquired):
        raise ValueError('acquisition records an attempt the cascade does not')
    return dict(sorted(dependencies.items()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    resolve = commands.add_parser('resolve', help='host-side cascade; fetches candidate datasheet URLs')
    resolve.add_argument('--root', required=True, help='capture root; all paths are relative to it')
    resolve.add_argument('--request', required=True, help=f'{REQUEST_SCHEMA} JSON relative to --root')
    resolve.add_argument('--out', required=True, help='new output directory relative to --root')
    resolve.add_argument('--allow-network', action='store_true',
                         help='owner opt-in for the Exa search tier (runs the exa-search CLI)')
    resolve.add_argument('--exa-command', default='exa-search', help='exa-search executable (default: on PATH)')
    resolve.add_argument('--timeout', type=float, default=30, help='seconds per datasheet fetch')
    resolve.add_argument('--search-timeout', type=float, default=60)
    resolve.add_argument('--min-interval', type=float, default=DEFAULT_INTERVAL,
                         help=f'seconds between Exa searches (minimum {MIN_INTERVAL})')
    validate = commands.add_parser('validate', help='offline replay of a published cascade')
    validate.add_argument('--root', required=True)
    validate.add_argument('--cascade', required=True, help=f'path of {REPORT} relative to --root')
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate':
            print(json.dumps({'dependencies': validate_cascade(args.root, args.cascade)}, indent=2))
            return 0
        from .parts_manifest import read_captured
        request = strict_json(read_captured(args.root, _rel(args.request)))
        command = None
        if args.allow_network:
            command = [shutil.which(args.exa_command) or args.exa_command]
        report = resolve_datasheets(args.root, request, args.out, allow_network=args.allow_network,
                                    exa_command=command, timeout=args.timeout,
                                    search_timeout=args.search_timeout, min_interval=args.min_interval)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, str(exc) + '\n')
    unresolved = sorted(k for k, p in report['parts'].items() if p['outcome'] != 'selected')
    print(json.dumps({'out': args.out, 'selection': report['selection'], 'unresolved': unresolved}, indent=2))
    return 2 if unresolved else 0


if __name__ == '__main__':
    raise SystemExit(main())
