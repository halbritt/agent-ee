"""Spec-record requirement accounting, using the ordinary scope/3 coverage compiler."""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import tempfile

from .baseline import compile_baseline
from .candidate import safe_relative
from .design_intent import exact_canonical, load_json
from .findings import safe_id
from .hardware_interface import exact_keys, text
from .spec_record import sha256, verify
from .storage import digest, json_bytes

SCHEMA = 'agent-ee.requirement-map/1'
RECEIPT_SCHEMA = 'agent-ee.requirement-map-receipt/1'
COMPILER = 'agent-ee.requirement-scope/3'
PROVENANCE = 'requirements-map'


class Refused(ValueError):
    """A well-formed requirement declaration cannot compile; findings name why."""

    def __init__(self, findings):
        super().__init__('; '.join(f['message'] for f in findings))
        self.findings = findings


def decode(raw):
    # Reuse duplicate-key and non-finite rejection, then decode ordinary scope
    # numbers. Exact source tokens remain bound by the retained map bytes.
    return json.loads(exact_canonical(load_json(raw)))


def map_declaration(raw):
    declaration = decode(raw)
    exact_keys(declaration, 'schema spec_record_sha256 scope requirements prose')
    if declaration['schema'] != SCHEMA:
        raise ValueError(f'expected {SCHEMA}')
    scope = declaration['scope']
    if not isinstance(scope, dict) or scope.get('schema') != 'agent-ee.scope/3':
        raise ValueError('map scope must be an agent-ee.scope/3 template')
    if 'checks' in scope or 'baseline' in scope:
        raise ValueError('map scope template must not supply checks or baseline')
    if not isinstance(scope.get('inputs'), list) or not scope['inputs']:
        raise ValueError('map scope must declare its input list')
    for spec in scope['inputs']:
        if not isinstance(spec, dict):
            raise ValueError('map scope input must be an object')
        text(spec.get('path'))
        safe_relative(text(spec.get('at')))
    for field in ('requirements', 'limitations'):
        if field in scope and not isinstance(scope[field], list):
            raise ValueError(f'map scope {field} must be a list')
    for field in ('schematic', 'board'):
        safe_relative(text(scope.get(field)))
    return declaration


def refuse(code, rid, message):
    raise Refused([{'code': code, 'id': rid, 'message': f'{rid}: {message}'}])


def indexed(entries, expected, label):
    if not isinstance(entries, list):
        raise ValueError(f'{label} must be a list')
    found = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
            raise ValueError(f'{label} entry needs an id')
        rid = entry['id']
        if rid in found:
            refuse('duplicate_mapping', rid, 'duplicate mapping')
        if rid not in expected:
            refuse('unknown_requirement', rid, 'not present in the spec record or declared envelope')
        found[rid] = entry
    missing = expected - found.keys()
    if missing:
        raise Refused([{'code': 'unmapped_requirement', 'id': rid,
                        'message': f'{rid}: no mapping'} for rid in sorted(missing)])
    return found


def disposition(entry, *, context=False):
    safe_id(entry['id'], 'requirement id')
    modes = {'checks', 'limitation', 'bench'} | ({'context'} if context else set())
    selected = modes.intersection(entry)
    if len(selected) != 1:
        refuse('mapping', entry['id'], 'exactly one of ' + ', '.join(sorted(modes)) + ' is required')
    mode = selected.pop()
    exact_keys(entry, 'id ' + mode)
    detail = entry[mode]
    if mode == 'checks':
        if not isinstance(detail, list) or not detail:
            refuse('unmapped_requirement', entry['id'], 'checks must be nonempty')
    else:
        fields = 'criterion procedure' if mode == 'bench' else 'reason'
        exact_keys(detail, fields)
        for field in fields.split():
            text(detail[field])
    if entry['id'] in ('POD-AC-8', 'POD-AC-9') and mode != 'bench':
        refuse('bench_only', entry['id'], 'pod acceptance item must be a bench obligation')
    return mode


def source_mappings(original, declarations, spec_raw, *, context=False, reserved=()):
    """Account for whole sources or an authored, byte-complete partition.

    Only prose may be context. Derived envelope obligations retain their exact
    field identity, and physical-only requirements stay physical in every clause.
    """
    kind = 'prose' if context else 'requirement'
    expected = {p['id']: p for p in original}
    mapped = indexed(declarations, set(expected), kind)
    sources, entries = [], []
    for rid, entry in mapped.items():
        source = expected[rid]
        if 'spans' not in entry:
            disposition(entry, context=context)
            sources.append(source)
            entries.append(entry)
            continue
        if 'baseline' in source:
            refuse('partition_source', rid, 'derived envelope requirements cannot be partitioned')
        exact_keys(entry, 'id spans')
        if not isinstance(entry['spans'], list) or not entry['spans']:
            refuse(kind + '_span', rid, 'spans must be nonempty')
        first, last = (source['locator'][k] for k in ('byte_start', 'byte_end'))
        cursor = first
        for span in entry['spans']:
            exact_keys(span, 'byte_start byte_end mapping')
            start, end = span['byte_start'], span['byte_end']
            if (type(start) is not int or type(end) is not int or
                    not cursor <= start < end <= last):
                refuse(kind + '_span', rid, 'overlapping, unordered or out-of-source span')
            if spec_raw[cursor:start].strip():
                refuse('uncovered_' + kind, rid, f'uncovered {kind} bytes {cursor}:{start}')
            try:
                quote = spec_raw[start:end].decode('utf-8')
            except UnicodeDecodeError:
                refuse(kind + '_span', rid, 'span splits a UTF-8 code point')
            text(quote)
            mapping = span['mapping']
            if not isinstance(mapping, dict) or 'id' not in mapping:
                raise ValueError(f'{rid}: span mapping needs an id')
            mode = disposition(mapping, context=context)
            if mapping['id'] in reserved:
                refuse('duplicate_mapping', mapping['id'], 'clause id collides with a source id')
            if rid in ('POD-AC-8', 'POD-AC-9') and mode != 'bench':
                refuse('bench_only', rid, 'every clause of a bench-only requirement must be a bench obligation')
            sources.append({'id': mapping['id'], 'text': quote, 'text_sha256': sha256(spec_raw[start:end]),
                            'locator': {**source['locator'], 'byte_start': start, 'byte_end': end},
                            'parent_id': rid})
            entries.append(mapping)
            cursor = end
        if spec_raw[cursor:last].strip():
            refuse('uncovered_' + kind, rid, f'uncovered {kind} bytes {cursor}:{last}')
    if not context:
        # Baseline requirements retain specification order; check execution
        # order remains the authored declaration order in entries.
        order = {source['id']: index for index, source in enumerate(original)}
        sources.sort(key=lambda source: order[source.get('parent_id', source['id'])])
    return sources, entries


def add_checks(entry, checks):
    from .capabilities import KINDS
    ids = []
    for spec in entry['checks']:
        exact_keys(spec, 'id kind params stage')
        cid = spec['id']
        safe_id(cid, 'check id')
        if not isinstance(spec['kind'], str) or spec['kind'] not in KINDS:
            refuse('unknown_kind', entry['id'], f'unregistered check kind {spec["kind"]!r}')
        if not isinstance(spec['params'], dict):
            raise ValueError(f'{cid}: params must be an object')
        if cid in ids:
            refuse('duplicate_check', entry['id'], f'repeated check {cid}')
        previous = next((c for c in checks if c['id'] == cid), None)
        if previous is not None and previous != spec:
            refuse('duplicate_check', entry['id'], f'conflicting declaration for {cid}')
        if previous is None:
            checks.append(copy.deepcopy(spec))
        ids.append(cid)
    return ids


@contextmanager
def compilation_inputs(inputs, files, provenance):
    """Keep caller inputs untouched while baseline readers inspect provenance."""
    if inputs is None:
        yield None
        return
    from .routing_rules import captured_file
    with tempfile.TemporaryDirectory(prefix='agent-ee-requirement-map-') as tmp:
        root = Path(tmp)
        for relative in files:
            if relative.startswith(PROVENANCE + '/'):
                continue
            source = captured_file(inputs, files, relative)
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        for name, raw in provenance.items():
            destination = root / PROVENANCE / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        yield root


def envelope_sources(scope, files, inputs):
    """Derive field obligations from captured bytes, with their original authority."""
    declared = scope.get('envelope')
    if declared is None:
        return []
    from . import board_envelope as envelope
    if inputs is None or files is None:
        raise ValueError('declared envelope requirements need captured inputs')
    envelope.fields(declared, 'scope envelope', ['path', 'profile', 'dialog'])
    raw = envelope.captured_bytes(files, inputs, declared['path'], 'envelope')
    profile = (envelope.captured_bytes(files, inputs, declared['profile'], 'profile')
               if declared['profile'] is not None else None)
    record = envelope.validate(raw, profile=profile, captured=(files, inputs))
    derived = envelope.derive_requirements(record, declared['path'])['requirements']
    return [dict(id=row['id'], text=row['statement'], locator=row['source']['locator'],
                 text_sha256=sha256(row['statement'].encode()),
                 baseline={key: row[key] for key in ('id', 'statement', 'source', 'intent')})
            for row in derived]


def account_requirements(declaration, record, spec_raw, *, files=None, inputs=None):
    """Build executable obligations and a receipt for every declared source."""
    sources = list(record['requirements'])
    derived = envelope_sources(declaration['scope'], files, inputs)
    collisions = {r['id'] for r in sources} & {r['id'] for r in derived}
    if collisions:
        refuse('duplicate_source', sorted(collisions)[0], 'spec and envelope requirement IDs collide')
    sources += derived
    reserved = {r['id'] for r in sources + record['prose']}
    sources, entries = source_mappings(sources, declaration['requirements'], spec_raw, reserved=reserved)
    prose_sources, prose_entries = source_mappings(record['prose'], declaration['prose'], spec_raw,
                                                  context=True, reserved=reserved)
    sources += prose_sources
    mapping = {}
    for entry in entries + prose_entries:
        rid = entry['id']
        if rid in mapping:
            refuse('duplicate_mapping', rid, 'id collides with another mapping')
        mapping[rid] = entry
    scope = copy.deepcopy(declaration['scope'])
    for spec in scope.get('inputs', []):
        target = str(safe_relative(spec['at']))
        if target == PROVENANCE or target.startswith(PROVENANCE + '/') or target == '.':
            raise ValueError('input destination reserved for compiler provenance')
    checks, requirements, receipts = [], [], []
    for entry in mapping.values():
        if disposition(entry, context=entry in prose_entries) == 'checks':
            add_checks(entry, checks)
    limitations = scope.setdefault('limitations', [])
    if not isinstance(limitations, list) or any(not isinstance(s, str) or not s.strip() for s in limitations):
        raise ValueError('scope limitations must be a list of nonempty strings')
    for source in sources:
        entry = mapping[source['id']]
        mode = disposition(entry, context=source in prose_sources)
        rid = safe_id(entry['id'], 'requirement id')
        receipt = {'id': rid, 'mode': mode, 'mapping': entry[mode],
                   'locator': source['locator'], 'text_sha256': source['text_sha256']}
        if 'parent_id' in source:
            receipt['parent_id'] = source['parent_id']
        if 'baseline' in source:
            receipt['source'] = source['baseline']['source']
        receipts.append(receipt)
        if mode == 'checks':
            ids = [check['id'] for check in entry['checks']]
            requirement = source.get('baseline', {'id': rid, 'statement': source['text'], 'intent': 'acceptance',
                'source': {'path': f'{PROVENANCE}/spec.md',
                           'locator': json.dumps(source['locator'], sort_keys=True)}})
            requirements.append({**requirement, 'checks': ids})
        elif mode == 'limitation':
            limitations.append(f'{rid}: NOT CHECKED: {entry[mode]["reason"]}')
        elif mode == 'bench':
            limitations.append(f'{rid}: BENCH REQUIRED: {entry[mode]["criterion"]}; '
                               f'procedure: {entry[mode]["procedure"]}')
    scope['checks'] = checks
    scope['baseline'] = {'requirements': requirements,
                         'targets': [f'{PROVENANCE}/spec-record.json', f'{PROVENANCE}/requirement-map.json'],
                         'reference_architectures': [f'{PROVENANCE}/spec.md']}
    return scope, receipts


def compile_map(map_raw, record_raw, spec_raw, *, files, inputs=None):
    """Compile against a captured inventory (or declared native scaffold paths).

    No tools execute here. Readable immutable contracts are validated through
    the same capability/baseline code used by candidate capture.
    """
    declaration = map_declaration(map_raw)
    if declaration['spec_record_sha256'] != sha256(record_raw):
        refuse('spec_record_hash', 'spec-record', 'map names different spec-record bytes')
    record = verify(record_raw, spec_raw)
    provenance = {'spec.md': spec_raw, 'spec-record.json': record_raw, 'requirement-map.json': map_raw}
    files = dict(files)
    if any(p == PROVENANCE or p.startswith(PROVENANCE + '/') for p in files):
        raise ValueError('input destination reserved for compiler provenance')
    for name, raw in provenance.items():
        path = f'{PROVENANCE}/{name}'
        files[path] = sha256(raw)
    from .capabilities import compile_coverage
    from .cli import validate_scope
    with compilation_inputs(inputs, files, provenance) as captured:
        scope, receipts = account_requirements(declaration, record, spec_raw, files=files, inputs=captured)
        scope.setdefault('inputs', []).append({'path': PROVENANCE, 'at': PROVENANCE})
        scope.setdefault('requirements', []).append(f'{PROVENANCE}/spec.md')
        # A map consisting entirely of limitations cannot create a passing scope.
        if not scope['baseline']['requirements']:
            compile_coverage(scope, files, captured)
        validate_scope(scope)
        baseline = compile_baseline(scope, files, captured)
    coverage = baseline['coverage']
    for receipt in receipts:
        if receipt['mode'] == 'checks':
            receipt['checks'] = [coverage['checks'][c['id']] for c in receipt['mapping']]
    receipt = {'schema': RECEIPT_SCHEMA,
               'compiler': {'version': COMPILER, 'module': __name__, 'source_sha256': digest(__file__)},
               'map_sha256': sha256(map_raw), 'spec_record_sha256': sha256(record_raw),
               'spec_sha256': sha256(spec_raw), 'scope_sha256': sha256(json_bytes(scope)),
               'baseline_id': baseline['baseline_id'], 'coverage_id': coverage['coverage_id'],
               'requirements': receipts, 'limitations': scope['limitations']}
    return scope, receipt


def compile_map_file(map_path, record_path, spec_path, *, inputs=None):
    from .candidate import source_manifest
    map_path = Path(map_path).resolve()
    raw = map_path.read_bytes()
    record_raw, spec_raw = Path(record_path).read_bytes(), Path(spec_path).read_bytes()
    template = map_declaration(raw)['scope']
    if inputs is None:
        files = {template['schematic']: None, template['board']: None}
        return compile_map(raw, record_raw, spec_raw, files=files)
    # Use the ordinary candidate input selector and hash its actual bytes. A
    # single captured root also lets all contract readers share the same paths.
    root = Path(inputs).resolve()
    selected, _, _ = source_manifest({'inputs': [{'path': str(root), 'at': 'captured'}]}, root)
    files = {path.removeprefix('captured/'): hashed for path, hashed in selected.items()}
    return compile_map(raw, record_raw, spec_raw, files=files, inputs=root)


def write_map_outputs(output, map_path, record_path, spec_path, scope, receipt):
    """Write only compiled metadata; the design scaffold remains its own producer."""
    payloads = {}
    for name, path, key in (('spec.md', spec_path, 'spec_sha256'),
                            ('spec-record.json', record_path, 'spec_record_sha256'),
                            ('requirement-map.json', map_path, 'map_sha256')):
        raw = Path(path).read_bytes()
        if sha256(raw) != receipt[key]:
            raise ValueError(f'{path}: source changed after compilation')
        payloads[name] = raw
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    provenance = output / PROVENANCE
    provenance.mkdir()
    for name, raw in payloads.items():
        (provenance / name).write_bytes(raw)
    (output / 'scope.json').write_bytes(json_bytes(scope))
    (output / 'receipt.json').write_bytes(json_bytes(receipt))
