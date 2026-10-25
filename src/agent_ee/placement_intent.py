"""The supported whole-board placement intent, before any native work."""
import math

SCHEMA = 'agent-ee.placement-intent/1'


def _object(value, path, required=(), optional=()):
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected object')
    for key in value:
        if key not in {*required, *optional}:
            raise ValueError(f'{path}.{key}: unsupported placement intent field')
    for key in required:
        if key not in value:
            raise ValueError(f'{path}.{key}: required field is missing')


def _rows(value, path):
    if not isinstance(value, list):
        raise ValueError(f'{path}: expected list')
    return ((f'{path}[{i}]', row) for i, row in enumerate(value))


def _text(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{path}: expected nonempty text')


def _number(value, path, *, nonnegative=False):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and math.isfinite(value * 1_000_000)
    except OverflowError:
        valid = False
    if not valid or (nonnegative and value < 0):
        raise ValueError(f'{path}: expected finite {"nonnegative " if nonnegative else ""}number')


def _texts(value, path):
    for item_path, item in _rows(value, path):
        _text(item, item_path)
    if not value or len(set(value)) != len(value):
        raise ValueError(f'{path}: expected nonempty list of distinct text')


def _notes(row, path):
    for key in ('basis', 'why', 'note', 'source', 'role'):
        if key in row:
            _text(row[key], f'{path}.{key}')


def _proximity_shape(row, path, *, require_both):
    """One authoritative shape check for a proximity row, shared by the validator and the adapter.

    ``max_mm`` is a finite non-negative number (never a bool); ``ref`` and ``near``
    are distinct non-empty text; ``pads`` is an object whose keys are only the
    subject and partner references, each a non-empty list of distinct pad names.
    The original dialect may omit the subject's pads (undeclared-subject fallback),
    so ``require_both`` is False there; the native form declares both.
    """
    for key in ('ref', 'near'):
        _text(row[key], path + '.' + key)
    if row['ref'] == row['near']:
        raise ValueError(path + '.near: proximity needs distinct footprints')
    _number(row['max_mm'], path + '.max_mm', nonnegative=True)
    required = (row['ref'], row['near']) if require_both else ()
    _object(row['pads'], path + '.pads', required, (row['ref'], row['near']))
    # The original dialect may leave either side undeclared (all non-aperture pads), including both.
    for ref, pads in row['pads'].items():
        _texts(pads, path + '.pads.' + ref)


def _locality_fields(document, path):
    """Locate unknown fields; the existing locality validator owns their meaning."""
    _object(document, path, ('schema', 'obligations'), ('source_records',))
    for opath, obligation in _rows(document['obligations'], path + '.obligations'):
        _object(obligation, opath, ('id', 'metric'),
                ('supply', 'return', 'switch', 'leg', 'kind', 'ic', 'inductor', 'return_net'))
        if obligation['metric'] != 'pad-centre-to-centre':
            raise ValueError(opath + '.metric: unsupported locality metric')
        for role in ('supply', 'return', 'switch', 'leg'):
            if role not in obligation:
                continue
            pair, ppath = obligation[role], opath + '.' + role
            _object(pair, ppath, ('from', 'to'), ('max_distance_nm', 'citation', 'provenance'))
            for end in ('from', 'to'):
                _object(pair[end], ppath + '.' + end, ('reference', 'pad', 'net'), ('uuid',))
            if 'provenance' not in pair:
                continue
            provenance, vpath = pair['provenance'], ppath + '.provenance'
            _object(provenance, vpath, optional=('class', 'basis', 'citation', 'decision_reference', 'derivation'))
            references = [(vpath + '.' + key, provenance[key]) for key in ('citation', 'decision_reference')
                          if key in provenance]
            if 'derivation' in provenance:
                derivation, dpath = provenance['derivation'], vpath + '.derivation'
                _object(derivation, dpath, optional=('kind', 'value_nm', 'distance_units',
                                                   'scale_nm_per_unit', 'scale_reference', 'source'))
                if 'source' in derivation:
                    references.append((dpath + '.source', derivation['source']))
            for rpath, reference in references:
                _object(reference, rpath, optional=('kind', 'path', 'sha256', 'locator', 'quote',
                    'owner', 'commit', 'repository', 'manufacturer', 'capture', 'fact'))


def validate(intent, groups):
    """Validate declarations in place, without filtering or changing captured bytes."""
    if isinstance(intent, dict):
        if isinstance(intent.get('assembly'), dict) and 'sides' in intent['assembly']:
            raise ValueError('intent.assembly.sides: side selection is unsupported')
        for path, row in _rows(intent.get('blocks', []), 'intent.blocks'):
            if isinstance(row, dict) and 'side' in row:
                raise ValueError(f'{path}.side: block side selection is unsupported')
    _object(intent, 'intent', optional=('schema', 'fixed_poses', 'edge_connectors',
                                      'proximity', 'electrical_locality'))
    if 'schema' in intent and intent['schema'] != SCHEMA:
        raise ValueError(f'intent.schema: expected {SCHEMA}; KRT floorplan intent needs explicit migration')
    for path, row in _rows(intent.get('fixed_poses', []), 'intent.fixed_poses'):
        _object(row, path, ('ref', 'x', 'y'), ('rot', 'side', 'basis', 'why'))
        _text(row['ref'], path + '.ref')
        for key in ('x', 'y', 'rot'):
            if key in row:
                _number(row[key], path + '.' + key)
        if 'rot' in row and row['rot'] % 90 != 0:
            raise ValueError(path + '.rot: expected a quarter turn')
        if 'side' in row and row['side'] not in ('F', 'B'):
            raise ValueError(path + '.side: expected F or B; side changes are unsupported')
        _notes(row, path)
    for path, row in _rows(intent.get('edge_connectors', []), 'intent.edge_connectors'):
        _object(row, path, ('ref', 'edge'), ('max_setback_mm', 'overhang_mm', 'class',
                                         'context', 'note', 'source'))
        _text(row['ref'], path + '.ref')
        if row['edge'] not in ('west', 'east', 'north', 'south'):
            raise ValueError(path + '.edge: expected west, east, north or south')
        if row.get('max_setback_mm') is not None:
            _number(row['max_setback_mm'], path + '.max_setback_mm', nonnegative=True)
        if 'overhang_mm' in row:
            interval = row['overhang_mm']
            subpath = path + '.overhang_mm'
            _object(interval, subpath, optional=('min', 'max'))
            if not interval:
                raise ValueError(subpath + ': expected a nonempty interval')
            for key, value in interval.items():
                _number(value, subpath + '.' + key, nonnegative=True)
            if ('max' in interval and interval['max'] < interval.get('min', 0)
                    or 'max' not in interval and interval.get('min', 0) > 0):
                raise ValueError(subpath + ': positive minimum needs a finite maximum at least as large')
        if 'class' in row and row['class'] not in ('edge_receptacle', 'connector_affinity'):
            raise ValueError(path + '.class: unsupported edge seat class')
        if 'context' in row:
            _object(row['context'], path + '.context', optional=('mount_mode',))
            if row['context'].get('mount_mode') not in ('edge_mount', 'top_mount'):
                raise ValueError(path + '.context.mount_mode: unsupported mount mode')
        _notes(row, path)
    for path, row in _rows(intent.get('proximity', []), 'intent.proximity'):
        _object(row, path, ('ref', 'near', 'max_mm', 'pads'), ('basis', 'context'))
        if row.get('basis') == 'pad_edge' and not isinstance(row.get('context'), dict) or (
                row.get('basis') == 'pad_edge' and 'limit' not in row['context']):
            raise ValueError(path + '.basis: pad_edge needs context.limit naming its authority; '
                             'derived_default stays an advisory objective')
        _proximity_shape(row, path, require_both=True)
        if 'context' in row:
            _object(row['context'], path + '.context', optional=('limit', 'basis', 'role', 'source'))
            for key, value in row['context'].items():
                _text(value, path + '.context.' + key)
        _notes(row, path)
    if 'electrical_locality' in intent:
        from .electrical_locality import validate as validate_locality
        _locality_fields(intent['electrical_locality'], 'intent.electrical_locality')
        try:
            validate_locality(intent['electrical_locality'])
        except ValueError as exc:
            raise ValueError('intent.electrical_locality: ' + str(exc)) from exc
    _object(groups, 'groups', required=('groups',), optional=('schema', 'board', 'rails'))
    if 'rails' in groups:
        _object(groups['rails'], 'groups.rails', optional=('shared', 'scoped'))
        for net, evidence in groups['rails'].get('shared', {}).items():
            _text(evidence, 'groups.rails.shared.' + net)
        for net, scope in groups['rails'].get('scoped', {}).items():
            _object(scope, 'groups.rails.scoped.' + net, optional=('intra_group', 'near_pairs'))
    if 'schema' in groups and (type(groups['schema']) is not int or groups['schema'] != 1):
        raise ValueError('groups.schema: expected legacy group schema 1 or omit schema')
    if 'board' in groups:
        _text(groups['board'], 'groups.board')
    names, members = set(), set()
    for path, row in _rows(groups['groups'], 'groups.groups'):
        _object(row, path, ('name', 'anchor', 'refs'), ('role', 'why', 'near'))
        _text(row['name'], path + '.name')
        _text(row['anchor'], path + '.anchor')
        _texts(row['refs'], path + '.refs')
        if 'near' in row:
            _texts(row['near'], path + '.near')
            declared = {g.get('name') for g in groups['groups'] if isinstance(g, dict)}
            for target in row['near']:
                if target == row['name'] or target not in declared:
                    raise ValueError(path + '.near: expected other declared group names; '
                                     f'{target!r} is not one')
        if row['name'] in names:
            raise ValueError(path + '.name: duplicate group name')
        if row['anchor'] not in row['refs']:
            raise ValueError(path + '.anchor: must belong to refs')
        if members.intersection(row['refs']):
            raise ValueError(path + '.refs: footprint belongs to multiple groups')
        names.add(row['name'])
        members.update(row['refs'])
        _notes(row, path)


# ---------------------------------------------------------------- AGENTEE-479: exact original-dialect adapter

COMPATIBILITY_SCHEMA = 'agent-ee.intent-compatibility/1'
DIALECT = {'kind': 'floorplan-intent', 'schema': 1, 'min_reader': 12, 'units': 'mm'}
DIALECT_TOP_LEVEL = frozenset(('assembly', 'blocks', 'board', 'context', 'decaps', 'defaults', 'dispositions',
                               'edge_connectors', 'envelope', 'fixed_poses', 'keepouts', 'kind',
                               'legality_budget', 'min_reader', 'must_lock', 'proximity', 'schema', 'units'))
EMPTY_NOOP = {'blocks': [], 'keepouts': [], 'must_lock': [], 'decaps': {},
              'context.cutouts': [], 'context.edge_contours': 0, 'context.file_locked': []}
CONTEXT_KEYS = frozenset(('basis', 'brief', 'budget_withheld', 'cutouts', 'decap_census', 'edge_contours',
                          'file_locked', 'keepouts_note', 'note', 'pose_claims_withheld', 'reconciliation'))
EDGE_CONTEXT_KEYS = frozenset(('basis', 'brief_row', 'cable_entry', 'compiled_from', 'mount_mode', 'role',
                               'was_class', 'was_source'))
PROXIMITY_CONTEXT_KEYS = frozenset(('basis', 'limit', 'role', 'source'))
DERIVED_DEFAULT = 'derived_default'


def _canonical_sha256(value):
    import hashlib
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def _exact(value, expected):
    """Equal and of the same exact type: True is never 1 and 1.0 is never 1 here."""
    return type(value) is type(expected) and value == expected


def is_dialect(document):
    """True for the one supported original dialect; native intents and anything else are not."""
    return isinstance(document, dict) and all(_exact(document.get(k), v) for k, v in DIALECT.items())


def _refuse_nonempty(document, path, key):
    expected = EMPTY_NOOP[key]
    value = document.get(key.split('.')[-1])
    if value != expected:
        raise ValueError(f'{path}: only the empty value {expected!r} is supported; '
                         'its semantics are not silently ignored')


def adapt(document, groups, *, source=None, supported=frozenset()):
    """Classify every field of an exact original-dialect intent without dropping or weakening any.

    Returns an ``agent-ee.intent-compatibility/1`` record. ``native_intent`` and
    ``native_groups`` are the projection the existing validator accepts today;
    every field that the projection cannot carry is listed in ``dispositions``
    with its exact path and in ``pending`` when a later step must act on it.
    ``runnable`` is False until no operative or objective disposition is pending,
    so an adapted intent can never run with part of its meaning quietly dropped.
    """
    if isinstance(document, dict) and document.get('schema') == SCHEMA:
        validate(document, groups)
        return {'schema': COMPATIBILITY_SCHEMA, 'dialect': 'native', 'source': source, 'withheld': {},
                'native_intent': document, 'native_groups': groups, 'dispositions': [],
                'unresolved_claims': [], 'pending': [], 'runnable': True}
    if not isinstance(document, dict):
        raise ValueError('intent: expected object')
    for key, expected in DIALECT.items():
        if not _exact(document.get(key), expected):
            raise ValueError(f'intent.{key}: expected {expected!r} of type {type(expected).__name__} '
                             f'(the only supported original dialect); got {document.get(key)!r}')
    unknown = sorted(set(document) - DIALECT_TOP_LEVEL)
    if unknown:
        raise ValueError('intent.' + unknown[0] + ': unsupported original-dialect field')
    dispositions, pending, unresolved = [], [], []

    def note(path, disposition, **detail):
        dispositions.append({'path': path, 'disposition': disposition, **detail})
        if disposition in ('operative_pending_capture', 'objective_pending_solver'):
            key = ('edge.side' if path.endswith('.side') and 'edge_connectors' in path else
                   'edge.cable_entry' if path.endswith('.cable_entry') else
                   'intent.proximity' if path.startswith('intent.proximity[') else
                   'groups.near' if path.endswith('.near') else path)
            if key in supported:
                dispositions[-1]['supported_by'] = 'placement_job capture/solver (AGENTEE-479)'
            else:
                pending.append({'path': path, 'needs': detail.get('needs', disposition)})

    # Empty no-op containers: accepted only when empty.
    for key in ('blocks', 'keepouts', 'must_lock', 'decaps'):
        if key in document:
            _refuse_nonempty(document, 'intent.' + key, key)
            note('intent.' + key, 'noop_empty')
    if 'defaults' in document:
        _object(document['defaults'], 'intent.defaults', optional=('zone_tolerance_mm',))
        if 'zone_tolerance_mm' in document['defaults']:
            _number(document['defaults']['zone_tolerance_mm'], 'intent.defaults.zone_tolerance_mm', nonnegative=True)
            note('intent.defaults.zone_tolerance_mm', 'noop_empty', because='blocks is empty')
    context = document.get('context', {})
    _object(context, 'intent.context', optional=tuple(CONTEXT_KEYS))
    for key in ('basis', 'pose_claims_withheld', 'budget_withheld'):
        if key in context:
            _object(context[key], 'intent.context.' + key, optional=tuple(context[key]) if isinstance(context[key], dict) else ())
            if key != 'pose_claims_withheld':  # withheld pose claims carry lists of references, not only text
                for name, value in context[key].items():
                    _text(value, f'intent.context.{key}.{name}')
    for key in ('keepouts_note', 'note'):
        if key in context:
            _text(context[key], 'intent.context.' + key)
    if 'decap_census' in context:
        _object(context['decap_census'], 'intent.context.decap_census',
                optional=tuple(context['decap_census']) if isinstance(context['decap_census'], dict) else ())
    if 'reconciliation' in context and not isinstance(context['reconciliation'], list):
        raise ValueError('intent.context.reconciliation: expected list')
    for key in ('cutouts', 'edge_contours', 'file_locked'):
        if key in context:
            _refuse_nonempty(context, 'intent.context.' + key, 'context.' + key)
            note('intent.context.' + key, 'noop_empty')
    # Unresolved mechanical claims stay visible and are never satisfied here.
    brief = context.get('brief', {})
    _object(brief, 'intent.context.brief', optional=('absent', 'consequences', 'contradictions', 'counts',
                                                    'declared', 'fixed', 'not_graded', 'path', 'product',
                                                    'unknown', 'unmatched', 'unmatched_checked'))
    for path, row in _rows(brief.get('consequences', []), 'intent.context.brief.consequences'):
        _object(row, path, ('id', 'ref', 'status'), ('basis', 'compiled_to', 'grader', 'value', 'why'))
        _text(row['id'], path + '.id')
        _text(row['ref'], path + '.ref')
        _text(row['status'], path + '.status')
        if row['status'] == 'unmeasured':
            unresolved.append({'id': row['id'], 'ref': row['ref'], 'status': 'unresolved_unmeasured',
                               'why': row.get('why'), 'path': path})
            note(path, 'unresolved_unmeasured', id=row['id'])
        else:
            note(path, 'descriptive', id=row['id'], status=row['status'], compiled_to=row.get('compiled_to'))
    for key in ('absent', 'contradictions', 'counts', 'declared', 'fixed', 'not_graded', 'path', 'product',
                'unknown', 'unmatched', 'unmatched_checked'):
        if key in brief:
            note('intent.context.brief.' + key, 'descriptive', sha256=_canonical_sha256(brief[key]))
    for key in ('basis', 'decap_census', 'keepouts_note', 'note', 'pose_claims_withheld', 'reconciliation'):
        if key in context:
            note('intent.context.' + key, 'descriptive', sha256=_canonical_sha256(context[key]))
    withheld = {}
    if 'dispositions' in document:
        _object(document['dispositions'], 'intent.dispositions', optional=('rules', 'withheld'))
        for key in ('rules', 'withheld'):
            table = document['dispositions'].get(key, {})
            _object(table, f'intent.dispositions.{key}', optional=tuple(table) if isinstance(table, dict) else ())
            for name, text in table.items():
                _text(text, f'intent.dispositions.{key}.{name}')
                withheld[f'{key}.{name}'] = text
                note(f'intent.dispositions.{key}.{name}', 'withheld_disposition', text=text)
    if 'budget_withheld' in context:
        for name, text in context['budget_withheld'].items():
            _text(text, 'intent.context.budget_withheld.' + name)
            withheld['budget.' + name] = text
            note('intent.context.budget_withheld.' + name, 'withheld_disposition', text=text)
    # Operative fields.
    native = {'schema': SCHEMA}
    if 'board' in document:
        _text(document['board'], 'intent.board')
        note('intent.board', 'descriptive')
    if 'assembly' in document:
        _object(document['assembly'], 'intent.assembly', ('sides',), ('why',))
        if document['assembly']['sides'] not in ('F', 'B'):
            raise ValueError('intent.assembly.sides: expected F or B')
        note('intent.assembly.sides', 'operative_pending_capture', value=document['assembly']['sides'],
             needs='verify every pad-bearing footprint is on the declared native side; no side-flip moves')
        if 'why' in document['assembly']:
            _text(document['assembly']['why'], 'intent.assembly.why')
            note('intent.assembly.why', 'descriptive')
    if 'envelope' in document:
        _object(document['envelope'], 'intent.envelope', ('rect', 'tolerance_mm'))
        rect = document['envelope']['rect']
        if not isinstance(rect, list) or len(rect) != 4:
            raise ValueError('intent.envelope.rect: expected [x0, y0, x1, y1]')
        for index, value in enumerate(rect):
            _number(value, f'intent.envelope.rect[{index}]')
        if not (rect[0] < rect[2] and rect[1] < rect[3]):
            raise ValueError('intent.envelope.rect: expected a positive-area rectangle')
        _number(document['envelope']['tolerance_mm'], 'intent.envelope.tolerance_mm', nonnegative=True)
        note('intent.envelope', 'operative_pending_capture', rect=rect,
             tolerance_mm=document['envelope']['tolerance_mm'],
             needs='verify the native Edge.Cuts bounding box matches rect within tolerance_mm')
    if 'legality_budget' in document:
        _object(document['legality_budget'], 'intent.legality_budget', ('oob_count',))
        if document['legality_budget']['oob_count'] != 0:
            raise ValueError('intent.legality_budget.oob_count: only 0 is supported')
        note('intent.legality_budget.oob_count', 'operative_supported', value=0,
             by='outline containment already requires zero violations')
    native['fixed_poses'] = []
    for path, row in _rows(document.get('fixed_poses', []), 'intent.fixed_poses'):
        _object(row, path, ('ref', 'x', 'y'), ('rot', 'side', 'basis', 'why'))
        native['fixed_poses'].append(dict(row))
        note(path, 'operative_supported', ref=row.get('ref'))
    native['edge_connectors'] = []
    for path, row in _rows(document.get('edge_connectors', []), 'intent.edge_connectors'):
        _object(row, path, ('ref', 'edge'), ('max_setback_mm', 'overhang_mm', 'side', 'class', 'context',
                                             'note', 'source'))
        projected = {k: row[k] for k in ('ref', 'edge', 'max_setback_mm', 'overhang_mm', 'class', 'note', 'source')
                     if k in row}
        ctx = row.get('context', {})
        _object(ctx, path + '.context', optional=tuple(EDGE_CONTEXT_KEYS))
        if 'mount_mode' in ctx:
            projected['context'] = {'mount_mode': ctx['mount_mode']}
        cable_entry = ctx.get('cable_entry')
        if cable_entry is not None and cable_entry not in ('perpendicular_top', 'in_plane'):
            raise ValueError(path + '.context.cable_entry: unsupported cable entry')
        if cable_entry == 'perpendicular_top' and row.get('side') != 'F':
            raise ValueError(path + ".context.cable_entry: perpendicular_top requires side 'F'")
        if 'side' in row and row['side'] not in ('F', 'B'):
            raise ValueError(path + '.side: expected F or B')
        for key in ('basis', 'brief_row', 'compiled_from', 'role', 'was_class', 'was_source'):
            if key in ctx:
                note(f'{path}.context.{key}', 'descriptive', sha256=_canonical_sha256(ctx[key]))
        if 'side' in row:
            note(path + '.side', 'operative_pending_capture', value=row['side'],
                 needs='verify the native face of the seated connector')
        if cable_entry is not None:
            note(path + '.context.cable_entry', 'operative_pending_capture', value=cable_entry,
                 needs='perpendicular_top: vertical access on the declared face; in_plane: declared board edge')
        native['edge_connectors'].append(projected)
        note(path, 'operative_supported', ref=row['ref'], edge=row['edge'])
    proximity = []
    for path, row in _rows(document.get('proximity', []), 'intent.proximity'):
        _object(row, path, ('ref', 'near', 'max_mm', 'basis', 'pads', 'context'))
        if row['basis'] != 'pad_edge':
            raise ValueError(path + '.basis: the original dialect declares pad_edge only')
        _proximity_shape(row, path, require_both=False)
        ctx = row['context']
        _object(ctx, path + '.context', ('limit',), tuple(PROXIMITY_CONTEXT_KEYS - {'limit'}))
        for key, value in ctx.items():
            _text(value, f'{path}.context.{key}')
        authority = 'advisory_objective' if ctx['limit'] == DERIVED_DEFAULT else 'unassessed_authority'
        proximity.append(dict(row))
        note(path, 'objective_pending_solver', ref=row['ref'], near=row['near'], max_mm=row['max_mm'],
             metric='kicad_route_c4af095_pad_rect_signed_gap', authority=authority, limit=ctx['limit'],
             needs='pad_edge signed rect-gap legs in the solver objective and the measured report; '
                   'derived_default stays advisory, a label other than derived_default is not sourced authority')
    native_groups = {k: groups[k] for k in ('schema', 'board') if isinstance(groups, dict) and k in groups}
    _object(groups, 'groups', required=('groups',), optional=('schema', 'board'))
    native_groups['groups'] = []
    names = [row.get('name') for row in groups['groups'] if isinstance(row, dict)]
    for path, row in _rows(groups['groups'], 'groups.groups'):
        _object(row, path, ('name', 'anchor', 'refs'), ('role', 'why', 'near'))
        projected = {k: row[k] for k in ('name', 'anchor', 'refs', 'role', 'why') if k in row}
        native_groups['groups'].append(projected)
        if 'near' in row:
            _texts(row['near'], path + '.near')
            for target in row['near']:
                if target == row['name'] or target not in names:
                    raise ValueError(path + '.near: expected other declared group names')
            note(path + '.near', 'objective_pending_solver', group=row['name'], near=list(row['near']),
                 needs='R1 declared-near interface objective and measured gap report')
        note(path, 'operative_supported', name=row['name'])
    validate(native, native_groups)
    record = {'schema': COMPATIBILITY_SCHEMA, 'dialect': dict(DIALECT), 'source': source,
              'native_intent': native, 'native_groups': native_groups,
              'proximity_pad_edge': proximity, 'dispositions': dispositions,
              'unresolved_claims': unresolved, 'withheld': withheld, 'pending': pending,
              'runnable': not pending}
    return record


def adapt_bytes(intent_raw, groups_raw):
    """Adapt exact captured bytes; the record carries their sha256 and never re-serialises them."""
    import hashlib
    from .design_intent import exact_canonical, load_json
    import json
    intent = json.loads(exact_canonical(load_json(intent_raw)))
    groups = json.loads(exact_canonical(load_json(groups_raw)))
    source = {'intent_sha256': hashlib.sha256(intent_raw).hexdigest(), 'intent_bytes': len(intent_raw),
              'groups_sha256': hashlib.sha256(groups_raw).hexdigest(), 'groups_bytes': len(groups_raw)}
    return adapt(intent, groups, source=source)
