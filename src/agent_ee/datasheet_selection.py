"""Replay explicit captured source decisions independently of purchase identity."""
import re
from pathlib import Path

from .catalog import exact_fields, nonempty, strict_json


def validate_ref(ref):
    from .parts_manifest import _blob
    exact_fields(ref, ('path', 'sha256', 'part_key'))
    _blob({k: ref[k] for k in ('path', 'sha256')})
    nonempty(ref['part_key'])


def validate_document(document, *, context):
    """Strict selection shape; an optional ``failed`` list records unacquired sources."""
    from .parts_manifest import _blob, _sha
    exact_fields(document, ('schema', 'parts'))
    if document['schema'] != 'agent-ee.datasheet-source-selection/1' or not isinstance(document['parts'], dict):
        raise ValueError(context + ': invalid datasheet source selection')
    for key, entry in document['parts'].items():
        nonempty(key)
        fields = ('selected', 'rejected', 'failed') if isinstance(entry, dict) and 'failed' in entry else (
            'selected', 'rejected')
        exact_fields(entry, fields)
        for field in fields:
            if not isinstance(entry[field], list):
                raise ValueError(context + ': source decisions must be lists')
            ids = set()
            for source in entry[field]:
                exact_fields(source, {'selected': ('id', 'sha256', 'provenance'), 'rejected': ('id', 'sha256', 'reason'),
                                      'failed': ('id', 'reason', 'provenance')}[field])
                nonempty(source['id'])
                if field != 'failed':
                    _sha(source['sha256'])
                if source['id'] in ids:
                    raise ValueError(context + ': duplicate source id ' + source['id'])
                ids.add(source['id'])
                if field != 'rejected':
                    _blob(source['provenance'], True)
                    if re.fullmatch(r'parts\[id=(.+)\]', source['provenance']['locator']) is None:
                        raise ValueError(context + ': source needs exact acquisition locator')
                if field != 'selected':
                    nonempty(source['reason'])
        # A source without acquired bytes can never be the selected one.
        failed = {s['id'] for s in entry.get('failed', [])}
        if failed & {s['id'] for s in entry['selected']}:
            raise ValueError(context + ': selected source is recorded as failed: ' + key)
    return document


def _selected_source(root, ref, *, context):
    """Return the sole selected source; rejected hashes always take precedence."""
    from .parts_manifest import _read_blob
    validate_ref(ref)
    document = validate_document(strict_json(_read_blob(root, ref)), context=context)
    entry = document['parts'].get(ref['part_key'])
    if entry is None or len(entry['selected']) != 1:
        ids = [] if entry is None else [s['id'] for s in entry['selected']]
        raise ValueError(f'{context}: missing or ambiguous source selection for {ref["part_key"]}: {ids}')
    selected = entry['selected'][0]
    conflicts = [s for s in entry['rejected'] if s['sha256'] == selected['sha256'] or s['id'] == selected['id']]
    if conflicts:
        raise ValueError(f'{context}: selected source {selected["id"]} is rejected: {conflicts}')
    return selected, entry['rejected']


def _receipt(root, selected):
    record, _ = _acquisition(root, selected['provenance'])
    if record['sha256'] != selected['sha256']:
        raise ValueError('selected source hash differs from acquisition')
    return {**selected, 'acquisition': record}


def selected_source(root, ref, *, context):
    selected, rejected = _selected_source(root, ref, context=context)
    return _receipt(root, selected), rejected


def check_binding(root, ref, artifact, provenance, *, context):
    """Refuse rejected bytes even under an acquisition alias or a different URL."""
    selected, rejected = _selected_source(root, ref, context=context)
    conflicts = [s for s in rejected if s['sha256'] == artifact['sha256']]
    if conflicts:
        raise ValueError(f'{context}: rejected datasheet source(s) {conflicts}; selected={selected["id"]}')
    if artifact['sha256'] != selected['sha256'] or provenance != selected['provenance']:
        raise ValueError(f'{context}: bound datasheet differs from selected source {selected["id"]} '
                         f'({selected["sha256"]}); bound={artifact}, provenance={provenance}')
    return _receipt(root, selected)


def acquisition_source(root, ref, *, context):
    selected, _ = _selected_source(root, ref, context=context)
    provenance = selected['provenance']
    acquisition_id = re.fullmatch(r'parts\[id=(.+)\]', provenance['locator'])[1]
    return {k: provenance[k] for k in ('path', 'sha256')}, acquisition_id


def _acquisition(root, provenance, reads=None):
    """Replay one source through the shared acquisition loader."""
    from .datasheet_facts import load_acquisition
    from .parts_manifest import _read_blob, captured_sha256
    match = re.fullmatch(r'parts\[id=(.+)\]', provenance['locator'])
    if match is None:
        raise ValueError('datasheet provenance must select a captured acquisition id')
    blob = {k: provenance[k] for k in ('path', 'sha256')}
    document = strict_json(_read_blob(root, blob, reads))
    bindings = sorted([{'id': r['id'], 'datasheet_url': r['url']} for r in document['parts']],
                      key=lambda r: r['id'])
    acquired, paths = load_acquisition(root, blob, bindings, reads)
    record = acquired.get(match[1])
    if record is None or record['missing']:
        raise ValueError('part datasheet acquisition missing')
    return record, {p: captured_sha256(root, p, reads) for p in paths}


def datasheet(root, part, reads=None):
    from .copper_features import require_purchased
    require_purchased(part, 'datasheet binding')
    sheet = part['datasheet']
    if sheet['status'] != 'captured':
        raise ValueError('binding fill requires captured datasheet: ' + part['id'])
    record, paths = _acquisition(root, sheet['provenance'], reads)
    artifact = {'path': (Path(sheet['provenance']['path']).parent / record['path']).as_posix(),
                'sha256': record['sha256']}
    if artifact != sheet['artifact']:
        raise ValueError('part datasheet artifact differs from acquisition')
    return record, paths
