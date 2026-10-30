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
from .run_record import validate_scope

SCHEMA = 'agent-ee.requirement-map/1'
SCHEMA_2 = 'agent-ee.requirement-map/2'
RECEIPT_SCHEMA = 'agent-ee.requirement-map-receipt/1'
RECEIPT_SCHEMA_2 = 'agent-ee.requirement-map-receipt/2'
OBLIGATIONS_SCHEMA = 'agent-ee.obligations/1'
COMPILER = 'agent-ee.requirement-scope/3'
PROVENANCE = 'requirements-map'
# AGENTEE-521: an authored obligation kind per source span selects which
# evidence modes may discharge that span. Derived envelope obligations keep
# their existing executable/bench accounting and are never partitioned.
OBLIGATION_KINDS = ('executable', 'operational', 'judgment', 'physical', 'context')
MODES_FOR_OBLIGATION = {'executable': ('checks', 'limitation'), 'operational': ('operational', 'limitation'),
                        'judgment': ('judgment', 'limitation'), 'physical': ('bench', 'limitation'),
                        'context': ('context',)}
DERIVED_MODES = ('checks', 'limitation', 'bench')
# The only operational criterion the build consumer can assess for M1. A
# generic "resume happened" never satisfies an arbitrary authored criterion.
OPERATIONAL_CRITERIA = ('public_build_and_resume',)
# Method class is derived from the executed check kind, never from a caller
# label. Kinds outside this table are unclassified and cover nothing.
KIND_CLASS = {
    **dict.fromkeys(('erc', 'drc', 'geometry', 'placement', 'board_envelope', 'fab_profile', 'fab_package',
                     'fab_export', 'fab_roundtrip', 'assembly_readiness', 'parts_subset', 'cpl_rotation'), 'native'),
    **dict.fromkeys(('netlist', 'netlist_parity', 'topology_rules', 'hardware_interface', 'net_names'), 'topology'),
    **dict.fromkeys(('interval_assertions', 'active_spice', 'rc_transient', 'dc_path'), 'model'),
    **dict.fromkeys(('measurements', 'dfm_geometry'), 'measurement'),
}
STATUS_ORDER = ('failed', 'unknown', 'pending', 'pass', 'not_applicable')


class Refused(ValueError):
    """A well-formed requirement declaration cannot compile; findings name why."""

    def __init__(self, findings):
        super().__init__('; '.join(f['message'] for f in findings))
        self.findings = findings


def decode(raw):
    # Reuse duplicate-key and non-finite rejection, then decode ordinary scope
    # numbers. Exact source tokens remain bound by the retained map bytes.
    return json.loads(exact_canonical(load_json(raw)))


def admit_source(raw, *, typed, source_files):
    """Admit a build's map binding and its reserved capture namespaces."""
    from .catalog import strict_json
    if (strict_json(raw).get('schema') == SCHEMA_2) != typed:
        raise ValueError('typed build-plan/2 requires map/2 and obligations; legacy map/1 cannot mix')
    for reserved in ('build-generated', PROVENANCE):
        if any(path == reserved or path.startswith(reserved + '/') for path in source_files):
            raise ValueError('build source occupies generated namespace: ' + reserved)


def map_declaration(raw):
    declaration = decode(raw)
    exact_keys(declaration, 'schema spec_record_sha256 scope requirements prose')
    if declaration['schema'] not in (SCHEMA, SCHEMA_2):
        raise ValueError(f'expected {SCHEMA} or {SCHEMA_2}')
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


def disposition(entry, *, context=False, typed=False):
    safe_id(entry['id'], 'requirement id')
    modes = {'checks', 'limitation', 'bench'} | ({'context'} if context else set())
    modes |= {'operational', 'judgment'} if typed else set()
    selected = modes.intersection(entry)
    if len(selected) != 1:
        refuse('mapping', entry['id'], 'exactly one of ' + ', '.join(sorted(modes)) + ' is required')
    mode = selected.pop()
    exact_keys(entry, 'id ' + mode)
    detail = entry[mode]
    if mode == 'checks':
        if not isinstance(detail, list) or not detail:
            refuse('unmapped_requirement', entry['id'], 'checks must be nonempty')
    elif mode == 'operational':
        exact_keys(detail, 'criterion')
        if not isinstance(detail['criterion'], str) or detail['criterion'] not in OPERATIONAL_CRITERIA:
            refuse('unsupported_criterion', entry['id'],
                   f'operational criterion {detail["criterion"]!r} is unsupported; supported: '
                   + ', '.join(OPERATIONAL_CRITERIA) + '. Prose is source context, never a predicate')
    elif mode == 'judgment':
        exact_keys(detail, 'record')
        safe_relative(text(detail['record']))
    else:
        fields = 'criterion procedure' if mode == 'bench' else 'reason'
        exact_keys(detail, fields)
        for field in fields.split():
            text(detail[field])
    if entry['id'] in ('POD-AC-8', 'POD-AC-9') and mode != 'bench':
        refuse('bench_only', entry['id'], 'pod acceptance item must be a bench obligation')
    return mode


def _partition_span(spec_raw, start, end, cursor, last, *, rid, span_code, uncovered_code, label, bound_label):
    if type(start) is not int or type(end) is not int or not cursor <= start < end <= last:
        refuse(span_code, rid, f'overlapping, unordered or out-of-{bound_label} span')
    if spec_raw[cursor:start].strip():
        refuse(uncovered_code, rid, f'uncovered {label} bytes {cursor}:{start}')
    try:
        quote = spec_raw[start:end].decode('utf-8')
    except UnicodeDecodeError:
        refuse(span_code, rid, 'span splits a UTF-8 code point')
    return quote, sha256(spec_raw[start:end])


def source_mappings(original, declarations, spec_raw, *, context=False, reserved=(), typed=False):
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
            disposition(entry, context=context, typed=typed)
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
            quote, text_hash = _partition_span(
                spec_raw, start, end, cursor, last, rid=rid,
                span_code=kind + '_span', uncovered_code='uncovered_' + kind,
                label=kind, bound_label='source')
            text(quote)
            mapping = span['mapping']
            if not isinstance(mapping, dict) or 'id' not in mapping:
                raise ValueError(f'{rid}: span mapping needs an id')
            mode = disposition(mapping, context=context, typed=typed)
            if mapping['id'] in reserved:
                refuse('duplicate_mapping', mapping['id'], 'clause id collides with a source id')
            if rid in ('POD-AC-8', 'POD-AC-9') and mode != 'bench':
                refuse('bench_only', rid, 'every clause of a bench-only requirement must be a bench obligation')
            sources.append({'id': mapping['id'], 'text': quote, 'text_sha256': text_hash,
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


def block_sources(scope, files, inputs):
    """Derived immutable block rows use the same accounting as envelope fields."""
    paths = scope.get('block_instances', [])
    if not isinstance(paths, list) or any(not isinstance(p,str) for p in paths) or paths != sorted(set(paths)):
        raise ValueError('block_instances must name unique captured instance records')
    from .block_adaptation import _instance, baseline_rows
    rows = []
    for path in paths:
        safe_relative(text(path))
        if inputs is None or path not in files or digest(Path(inputs)/path) != files[path]:
            raise ValueError('block instance record must be captured unchanged')
        record, source = _instance(inputs, path)
        dependencies=[record['block']+'/block.json',*[record['block']+'/'+p for p in source['files']]]
        if 'composition' in record:dependencies.append(record['composition']['path'])
        if any(p not in files or digest(Path(inputs)/p)!=files[p] for p in dependencies):
            raise ValueError('block dependency must be captured unchanged')
        bound = baseline_rows(record, source)
        bound.append(dict(id=record['id']+'-boundary', statement='Preserve selected source identities and pin connectivity',
            source=dict(path=path, locator='bindings'), checks=[record['id']+'-provenance'], intent='acceptance'))
        rows += [dict(id=r['id'], text=r['statement'], locator=r['source']['locator'],
            text_sha256=sha256(r['statement'].encode()), baseline={k:r[k] for k in ('id','statement','source','intent')}) for r in bound]
    ids = [r['id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate block requirement identity')
    return rows


def load_obligations(raw, record, spec_raw):
    """Authored obligation kinds bound to byte-complete partitions of every captured clause.

    Each span names its exact spec bytes and their hash; an edited clause
    therefore invalidates its obligation. Returns {clause id: [spans]}.
    """
    declaration = decode(raw)
    exact_keys(declaration, 'schema spec_sha256 clauses')
    if declaration['schema'] != OBLIGATIONS_SCHEMA:
        raise ValueError(f'expected {OBLIGATIONS_SCHEMA}')
    if declaration['spec_sha256'] != sha256(spec_raw):
        refuse('obligation_spec', 'obligations', 'obligations name different spec bytes')
    if not isinstance(declaration['clauses'], list):
        raise ValueError('obligations clauses must be a list')
    clauses = {c['id']: ('requirement', c) for c in record['requirements']}
    clauses.update({c['id']: ('prose', c) for c in record['prose']})
    bound = {}
    for entry in declaration['clauses']:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
            raise ValueError('obligation entry needs an id')
        rid = entry['id']
        exact_keys(entry, 'id spans')
        if rid not in clauses:
            refuse('obligation_unknown_id', rid, 'not a captured spec clause')
        if rid in bound:
            refuse('duplicate_mapping', rid, 'duplicate obligation entry')
        kind, clause = clauses[rid]
        if not isinstance(entry['spans'], list) or not entry['spans']:
            refuse('obligation_partition', rid, 'spans must be nonempty')
        first, last = (clause['locator'][k] for k in ('byte_start', 'byte_end'))
        cursor, spans = first, []
        for span in entry['spans']:
            exact_keys(span, 'byte_start byte_end text_sha256 obligation')
            start, end = span['byte_start'], span['byte_end']
            _, text_hash = _partition_span(
                spec_raw, start, end, cursor, last, rid=rid,
                span_code='obligation_partition', uncovered_code='obligation_partition',
                label='clause', bound_label='clause')
            if span['text_sha256'] != text_hash:
                refuse('obligation_text_drift', rid, f'span {start}:{end} hash differs from the spec bytes')
            obligation = span['obligation']
            if obligation not in OBLIGATION_KINDS:
                refuse('obligation_kind', rid, f'unknown obligation kind {obligation!r}')
            if obligation == 'context' and kind != 'prose':
                refuse('obligation_kind', rid, 'only prose may be context')
            if rid in ('POD-AC-8', 'POD-AC-9') and obligation != 'physical':
                refuse('bench_only', rid, 'pod acceptance item must be a physical obligation')
            spans.append({'byte_start': start, 'byte_end': end, 'text_sha256': span['text_sha256'],
                          'obligation': obligation})
            cursor = end
        if spec_raw[cursor:last].strip():
            refuse('obligation_partition', rid, f'uncovered clause bytes {cursor}:{last}')
        bound[rid] = spans
    missing = set(clauses) - set(bound)
    if missing:
        raise Refused([{'code': 'obligation_missing', 'id': rid, 'message': f'{rid}: no obligation'}
                       for rid in sorted(missing)])
    return bound


def input_inventory(root):
    """Ordinary files under root by relative path; any file or directory link or special file refuses."""
    from .stage_receipts import file_tree
    files, other = file_tree(root)
    if other:
        raise ValueError('build inputs contain links or special files: ' + str(other))
    return files


def compile_obligations(record, spec_raw, obligation_map):
    """Compile an obligations/1 document from per-clause span declarations and spec bytes."""
    clauses = []
    for source in record['requirements'] + record['prose']:
        rid = source['id']
        if rid not in obligation_map:
            raise ValueError(f'overlay declares no obligation for captured clause {rid}')
        spans = []
        for span in obligation_map[rid]:
            exact_keys(span, 'byte_start byte_end obligation')
            start, end = span['byte_start'], span['byte_end']
            spans.append({'byte_start': start, 'byte_end': end,
                          'text_sha256': sha256(spec_raw[start:end]), 'obligation': span['obligation']})
        clauses.append({'id': rid, 'spans': spans})
    extra = set(obligation_map) - {c['id'] for c in clauses}
    if extra:
        raise ValueError('overlay obligations name unknown clauses: ' + ', '.join(sorted(extra)))
    return {'schema': OBLIGATIONS_SCHEMA, 'spec_sha256': sha256(spec_raw), 'clauses': clauses}


def bind_receipt(receipt, spec_raw, *, source_hashes, accounting, provisional=False):
    """Verify that a requirement-map receipt matches captured inputs, accounting, and UTF-8 spec spans."""
    if provisional:
        receipt.update(source_hashes)
    if any(receipt.get(field) != pin for field, pin in source_hashes.items()):
        raise ValueError('Requirement receipt differs from captured source inputs')
    fields = ('id', 'parent_id', 'mode', 'mapping', 'locator', 'text_sha256', 'obligation',
              'obligation_text_sha256', 'bound_ids', 'source')
    expected_rows = {row['id']: {key: row.get(key) for key in fields} for row in accounting}
    actual_rows = {row['id']: {key: row.get(key) for key in fields} for row in receipt['requirements']}
    if len(actual_rows) != len(receipt['requirements']) or actual_rows != expected_rows:
        raise ValueError('Requirement receipt differs from sealed requirements accounting')
    for entry in receipt['requirements']:
        locator = entry['locator']
        if isinstance(locator, dict) and {'byte_start', 'byte_end'} <= locator.keys():
            start, end = locator['byte_start'], locator['byte_end']
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(spec_raw):
                raise ValueError('Requirement receipt has an invalid source span')
            try:
                spec_raw[:start].decode('utf-8')
                spec_raw[start:end].decode('utf-8')
            except UnicodeError as exc:
                raise ValueError('Requirement receipt splits a source code point') from exc
            pin = sha256(spec_raw[start:end])
            if entry.get('obligation_text_sha256') != pin or entry['text_sha256'] != pin:
                raise ValueError('Requirement receipt source span hash is stale')
        elif entry['obligation'] not in ('executable', 'physical') or not entry.get('source'):
            raise ValueError('Typed non-spec obligation is not an executable envelope source')


def bind_obligation(source, mode, obligations):
    """The obligation span coinciding exactly with this mapped source, or a refusal."""
    parent = source.get('parent_id', source['id'])
    if 'baseline' in source:
        # Derived envelope obligations keep their existing executable/bench accounting.
        if mode not in DERIVED_MODES:
            refuse('incompatible_evidence_method', source['id'],
                   f'derived envelope obligation cannot be discharged by {mode}')
        return {'obligation': 'executable', 'obligation_text_sha256': source['text_sha256']}
    start, end = source['locator']['byte_start'], source['locator']['byte_end']
    span = next((s for s in obligations.get(parent, []) if (s['byte_start'], s['byte_end']) == (start, end)), None)
    if span is None:
        refuse('span_alignment', source['id'],
               f'mapped bytes {start}:{end} coincide with no obligation span of {parent}; '
               'a map may not merge or split obligation spans')
    if mode not in MODES_FOR_OBLIGATION[span['obligation']]:
        refuse('incompatible_evidence_method', source['id'],
               f'bytes {start}:{end} carry obligation {span["obligation"]!r}; mode {mode!r} cannot discharge it '
               f'(allowed: {", ".join(MODES_FOR_OBLIGATION[span["obligation"]])})')
    return {'obligation': span['obligation'], 'obligation_text_sha256': span['text_sha256']}


def account_requirements(declaration, record, spec_raw, *, files=None, inputs=None, obligations=None):
    """Build executable obligations and a receipt for every declared source.

    A requirement-map/2 declaration is typed: every mapped source must coincide
    with an authored obligation span whose kind admits the chosen mode.
    """
    typed = declaration['schema'] == SCHEMA_2
    if typed and obligations is None:
        refuse('obligations_required', 'obligations', f'{SCHEMA_2} requires an {OBLIGATIONS_SCHEMA} declaration')
    if not typed and obligations is not None:
        refuse('obligations_unexpected', 'obligations', f'{SCHEMA} cannot bind obligations; author {SCHEMA_2}')
    sources = list(record['requirements'])
    derived = envelope_sources(declaration['scope'], files, inputs) + block_sources(declaration['scope'], files, inputs)
    if len({r['id'] for r in derived}) != len(derived):
        raise ValueError('duplicate derived envelope/block requirement identity')
    collisions = {r['id'] for r in sources} & {r['id'] for r in derived}
    if collisions:
        refuse('duplicate_source', sorted(collisions)[0], 'spec and envelope requirement IDs collide')
    sources += derived
    reserved = {r['id'] for r in sources + record['prose']}
    sources, entries = source_mappings(sources, declaration['requirements'], spec_raw, reserved=reserved,
                                       typed=typed)
    prose_sources, prose_entries = source_mappings(record['prose'], declaration['prose'], spec_raw,
                                                  context=True, reserved=reserved, typed=typed)
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
        if disposition(entry, context=entry in prose_entries, typed=typed) == 'checks':
            add_checks(entry, checks)
    if scope.get('block_instances') and scope.get('stages'):
        order=scope['stages']['order']
        # Derived block rows follow authored rows in the map, but their checks
        # must run before the generation package is sealed. Keep stage order
        # and the existing order within both the checks and the package tail.
        from .fab_package import BOUND_FOLLOWERS
        done=(scope.get('generation') or {}).get('done_check')
        def check_order(check):
            follower=BOUND_FOLLOWERS.get(check['kind'])
            terminal=done is not None and (check['id']==done or
                (follower is not None and check.get('params',{}).get(follower)==done))
            return order.index(check['stage']), terminal
        checks.sort(key=check_order)
    limitations = scope.setdefault('limitations', [])
    if not isinstance(limitations, list) or any(not isinstance(s, str) or not s.strip() for s in limitations):
        raise ValueError('scope limitations must be a list of nonempty strings')
    for source in sources:
        entry = mapping[source['id']]
        mode = disposition(entry, context=source in prose_sources, typed=typed)
        rid = safe_id(entry['id'], 'requirement id')
        receipt = {'id': rid, 'mode': mode, 'mapping': entry[mode],
                   'locator': source['locator'], 'text_sha256': source['text_sha256']}
        if 'parent_id' in source:
            receipt['parent_id'] = source['parent_id']
        if 'baseline' in source:
            receipt['source'] = source['baseline']['source']
        if typed:
            receipt.update(bind_obligation(source, mode, obligations))
            receipt['bound_ids'] = [c['id'] for c in entry['checks']] if mode == 'checks' else []
        receipts.append(receipt)
        if mode == 'operational':
            limitations.append(f'{rid}: OPERATIONAL PENDING: {entry[mode]["criterion"]}; '
                               'assessed by the build consumer from sealed receipts, never by checks')
        elif mode == 'judgment':
            limitations.append(f'{rid}: JUDGMENT PENDING: record {entry[mode]["record"]}; '
                               'admitted by the build consumer, never a check result')
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


def compile_map(map_raw, record_raw, spec_raw, *, files, inputs=None, obligations_raw=None):
    """Compile against a captured inventory (or declared native scaffold paths).

    No tools execute here. Readable immutable contracts are validated through
    the same capability/baseline code used by candidate capture. A map/1
    compiles exactly as before; a map/2 also needs its obligations bytes.
    """
    declaration = map_declaration(map_raw)
    if declaration['spec_record_sha256'] != sha256(record_raw):
        refuse('spec_record_hash', 'spec-record', 'map names different spec-record bytes')
    record = verify(record_raw, spec_raw)
    typed = declaration['schema'] == SCHEMA_2
    if typed and obligations_raw is None:
        refuse('obligations_required', 'obligations', f'{SCHEMA_2} requires an {OBLIGATIONS_SCHEMA} declaration')
    if not typed and obligations_raw is not None:
        refuse('obligations_unexpected', 'obligations', f'{SCHEMA} cannot bind obligations; author {SCHEMA_2}')
    obligations = load_obligations(obligations_raw, record, spec_raw) if typed else None
    provenance = {'spec.md': spec_raw, 'spec-record.json': record_raw, 'requirement-map.json': map_raw}
    if typed:
        provenance['obligations.json'] = obligations_raw
    files = dict(files)
    if any(p == PROVENANCE or p.startswith(PROVENANCE + '/') for p in files):
        raise ValueError('input destination reserved for compiler provenance')
    for name, raw in provenance.items():
        path = f'{PROVENANCE}/{name}'
        files[path] = sha256(raw)
    from .capabilities import compile_coverage
    with compilation_inputs(inputs, files, provenance) as captured:
        scope, receipts = account_requirements(declaration, record, spec_raw, files=files, inputs=captured,
                                               obligations=obligations)
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
    receipt = {'schema': RECEIPT_SCHEMA_2 if typed else RECEIPT_SCHEMA,
               'compiler': {'version': COMPILER, 'module': __name__, 'source_sha256': digest(__file__)},
               'map_sha256': sha256(map_raw), 'spec_record_sha256': sha256(record_raw),
               'spec_sha256': sha256(spec_raw), 'scope_sha256': sha256(json_bytes(scope)),
               'baseline_id': baseline['baseline_id'], 'coverage_id': coverage['coverage_id'],
               'requirements': receipts, 'limitations': scope['limitations']}
    if typed:
        receipt['obligations_sha256'] = sha256(obligations_raw)
        receipt['method_binding'] = 'typed'
    return scope, receipt


def method_class(kind):
    """Evidence class of an executed check kind; unknown kinds cover nothing."""
    return KIND_CLASS.get(kind, 'unclassified')


def _worst(statuses):
    statuses = [s for s in statuses if s != 'not_applicable'] or ['not_applicable']
    return min(statuses, key=STATUS_ORDER.index)


def coverage_view(receipt, result_rows):
    """Per-clause, per-span coverage from a compiled receipt and executed check rows.

    ``result_rows`` are ordinary result/1 check rows ({id, kind, execution,
    outcome, ...}); their executed ``kind`` selects the method class. This
    view cannot assess operational, judgment or physical evidence: those
    spans are ``pending`` until the build consumer supplies its bound
    assessment. A receipt/1 has no method binding and reports ``unknown``.
    """
    if not isinstance(receipt, dict) or receipt.get('schema') not in (RECEIPT_SCHEMA, RECEIPT_SCHEMA_2):
        raise ValueError('expected a requirement-map receipt')
    rows = {}
    for row in result_rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str):
            raise ValueError('check rows need an id')
        if row['id'] in rows:
            raise ValueError(f'duplicate check row {row["id"]}')
        rows[row['id']] = row
    legacy = receipt['schema'] == RECEIPT_SCHEMA
    view = {}
    for entry in receipt['requirements']:
        parent = entry.get('parent_id', entry['id'])
        clause = view.setdefault(parent, {'spans': [], 'status': None})
        # Derived envelope rows carry the envelope field locator, not spec bytes.
        locator = entry['locator'] if isinstance(entry['locator'], dict) else {}
        span = {'id': entry['id'], 'byte_start': locator.get('byte_start'), 'byte_end': locator.get('byte_end'),
                'mode': entry['mode'], 'executed': []}
        if legacy:
            span.update(obligation=None, bound_ids=[], status='unknown',
                        reason='legacy map/1: method binding unqualified')
            clause['spans'].append(span)
            continue
        span.update(obligation=entry['obligation'], bound_ids=list(entry['bound_ids']))
        mode = entry['mode']
        if mode == 'checks':
            statuses = []
            for cid in span['bound_ids']:
                row = rows.get(cid)
                if row is None:
                    statuses.append('pending')
                    continue
                executed = {'id': cid, 'kind': row.get('kind'), 'class': method_class(row.get('kind')),
                            'execution': row.get('execution'), 'outcome': row.get('outcome')}
                span['executed'].append(executed)
                if executed['class'] == 'unclassified':
                    statuses.append('unknown')
                elif executed['execution'] == 'ok' and executed['outcome'] in ('pass', 'info'):
                    statuses.append('pass')
                elif executed['execution'] == 'ok' and executed['outcome'] == 'defect':
                    statuses.append('failed')
                else:
                    statuses.append('unknown')
            span['status'] = _worst(statuses) if statuses else 'pending'
            if span['status'] == 'unknown' and any(e['class'] == 'unclassified' for e in span['executed']):
                span['reason'] = 'unclassified check kind supplies no method coverage'
        elif mode == 'limitation':
            span.update(status='failed', reason='NOT CHECKED: ' + entry['mapping']['reason'])
        elif mode in ('operational', 'judgment'):
            span.update(status='pending', reason=f'{mode} evidence is assessed by the build consumer')
        elif mode == 'bench':
            span.update(status='pending', reason='physical obligation; no bench result admitted here')
        else:
            span.update(status='not_applicable', reason='prose context')
        clause['spans'].append(span)
    for clause in view.values():
        clause['status'] = _worst([s['status'] for s in clause['spans']])
    return view


def compile_map_file(map_path, record_path, spec_path, *, inputs=None, obligations_path=None):
    from .candidate import source_manifest
    map_path = Path(map_path).resolve()
    raw = map_path.read_bytes()
    record_raw, spec_raw = Path(record_path).read_bytes(), Path(spec_path).read_bytes()
    obligations_raw = Path(obligations_path).read_bytes() if obligations_path is not None else None
    template = map_declaration(raw)['scope']
    if inputs is None:
        files = {template['schematic']: None, template['board']: None}
        return compile_map(raw, record_raw, spec_raw, files=files, obligations_raw=obligations_raw)
    # Use the ordinary candidate input selector and hash its actual bytes. A
    # single captured root also lets all contract readers share the same paths.
    root = Path(inputs).resolve()
    selected, _, _ = source_manifest({'inputs': [{'path': str(root), 'at': 'captured'}]}, root)
    files = {path.removeprefix('captured/'): hashed for path, hashed in selected.items()}
    return compile_map(raw, record_raw, spec_raw, files=files, inputs=root, obligations_raw=obligations_raw)


def write_map_outputs(output, map_path, record_path, spec_path, scope, receipt, *, obligations_path=None):
    """Write only compiled metadata; the design scaffold remains its own producer."""
    payloads = {}
    sources = [('spec.md', spec_path, 'spec_sha256'), ('spec-record.json', record_path, 'spec_record_sha256'),
               ('requirement-map.json', map_path, 'map_sha256')]
    if (obligations_path is not None) != (receipt.get('schema') == RECEIPT_SCHEMA_2):
        raise ValueError('obligations path must accompany exactly a requirement-map-receipt/2')
    if obligations_path is not None:
        sources.append(('obligations.json', obligations_path, 'obligations_sha256'))
    for name, path, key in sources:
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
