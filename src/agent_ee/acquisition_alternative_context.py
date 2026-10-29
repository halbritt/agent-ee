"""Finite captured-context admission/copy for acquisition preparation; no fetches."""
from pathlib import Path

from . import acquisition_alternative, jlc_stock_capture, parts_manifest, requirement_map
from .catalog import exact_fields, strict_json
from .storage import json_bytes

SCHEMA = 'agent-ee.acquisition-alternative-context/1'
AT = 'preparation/alternative-context'


def read(root, raw):
    """Read the explicit finite closure; no unrelated draft outputs are copied."""
    root = Path(root).absolute()
    if root.resolve(strict=True) != root:
        raise ValueError('alternative source root contains an alias')
    context = strict_json(raw)
    fields = ('schema', 'reference', 'files', 'catalog', 'carrier_page', 'ordering_text', 'parents', 'derivation')
    exact_fields(context, (*fields, 'supplier_capture') if 'supplier_capture' in context else fields)
    if context['schema'] != SCHEMA or context['reference'] != 'acquisition-control':
        raise ValueError('unsupported acquisition alternative context')
    files = context['files']
    if not isinstance(files, dict) or not 1 <= len(files) <= 64:
        raise ValueError('alternative context requires a finite 1..64-file closure')
    captures = {path: parts_manifest._read_blob(root, dict(path=path, sha256=sha))
                for path, sha in files.items()}
    refs = [context[k] for k in ('catalog', 'carrier_page', 'ordering_text', 'derivation')]
    if not isinstance(context['parents'], list) or len(context['parents']) != 2:
        raise ValueError('this context supports exactly two retained catalog parents')
    refs += context['parents']
    if 'supplier_capture' in context:
        from . import acquisition_selected_cpl
        exact_fields(context['supplier_capture'], ('provenance', 'raw', 'http_response', 'receipt'))
        refs += list(context['supplier_capture'].values())
    for blob in refs:
        parts_manifest._blob(blob)
        if files.get(blob['path']) != blob['sha256']:
            raise ValueError('context source reference is outside its pinned closure')
    try:
        _union(context, captures)
        if 'supplier_capture' in context:
            acquisition_selected_cpl.supplier_files(context, captures)
    except (KeyError, TypeError, UnicodeError) as exc:
        raise ValueError('malformed alternative catalog/derivation evidence') from exc
    return context, captures


def _union(context, captures):
    """Check the reviewed union algebra without resetting any parent timestamps."""
    catalog_path = Path(context['catalog']['path'])
    catalog = strict_json(captures[str(catalog_path)])
    parents = [strict_json(captures[b['path']]) for b in context['parents']]
    if parents[0]['source'] != parents[1]['source']:
        raise ValueError('catalog parents have different source contracts')
    derived = dict(schema=jlc_stock_capture.SCHEMA,
        as_of=min((d['as_of'] for d in parents), key=lambda t: jlc_stock_capture._utc(t, 'as_of')),
        completed_at=max((d['completed_at'] for d in parents), key=lambda t: jlc_stock_capture._utc(t, 'completed_at')),
        stale_before=None, source=parents[0]['source'], requests=[], parts={})
    needed = {b['path'] for b in context['parents']}
    needed.update(context[k]['path'] for k in ('catalog', 'carrier_page', 'ordering_text', 'derivation'))
    if 'supplier_capture' in context:
        needed.update(b['path'] for b in context['supplier_capture'].values())
    seen = set()
    for parent in parents:
        for request in parent['requests']:
            if request['lcsc'] in seen:
                raise ValueError('overlapping parent catalog observations')
            seen.add(request['lcsc'])
        def response(rel):
            path = (catalog_path.parent / rel).as_posix()
            needed.add(path)
            return captures[path]
        jlc_stock_capture.validate_capture(parent, response)
        derived['requests'] += parent['requests']
        derived['parts'].update(parent['parts'])
    if catalog != derived:
        raise ValueError('catalog is not the exact conservative retained-parent union')
    derivation = strict_json(captures[context['derivation']['path']])
    if ([p['sha256'] for p in derivation['parents']] != [b['sha256'] for b in context['parents']] or
            'NOT a fresh capture' not in derivation['kind']):
        raise ValueError('union derivation does not retain parent identities/offline status')
    if needed != set(captures):
        raise ValueError('context closure contains missing or unrelated files')


def apply(revised, context, captures, as_of):
    """Copy exact admitted sources before selection and the existing typed seal."""
    destination = Path(revised) / AT
    if destination.exists():
        raise ValueError('alternative context destination already exists')
    for name, raw in captures.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    def rebase(key):
        return {**context[key], 'path': AT + '/' + context[key]['path']}
    selected = acquisition_alternative.CapturedContext(
        requirement_map.input_inventory(revised), rebase('catalog'), rebase('carrier_page'),
        rebase('ordering_text'), as_of)
    report = acquisition_alternative.revise(revised, selected)
    # This report is an input, not a native receipt or a post-seal amendment.
    (Path(revised) / 'preparation/alternative-report.json').write_bytes(json_bytes(report))
    return report
