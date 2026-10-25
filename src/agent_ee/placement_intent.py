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
        if row.get('basis') == 'pad_edge':
            raise ValueError(path + '.basis: pad_edge is unsupported; proximity scores minimum '
                             'Chebyshev pad-centre distance, not pad-edge distance')
        for key in ('ref', 'near'):
            _text(row[key], path + '.' + key)
        if row['ref'] == row['near']:
            raise ValueError(path + '.near: proximity needs distinct footprints')
        _number(row['max_mm'], path + '.max_mm', nonnegative=True)
        _object(row['pads'], path + '.pads', (row['ref'], row['near']))
        for ref, pads in row['pads'].items():
            _texts(pads, path + '.pads.' + ref)
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
    _object(groups, 'groups', required=('groups',), optional=('schema', 'board'))
    if 'schema' in groups and (type(groups['schema']) is not int or groups['schema'] != 1):
        raise ValueError('groups.schema: expected legacy group schema 1 or omit schema')
    if 'board' in groups:
        _text(groups['board'], 'groups.board')
    names, members = set(), set()
    for path, row in _rows(groups['groups'], 'groups.groups'):
        _object(row, path, ('name', 'anchor', 'refs'), ('role', 'why'))
        _text(row['name'], path + '.name')
        _text(row['anchor'], path + '.anchor')
        _texts(row['refs'], path + '.refs')
        if row['name'] in names:
            raise ValueError(path + '.name: duplicate group name')
        if row['anchor'] not in row['refs']:
            raise ValueError(path + '.anchor: must belong to refs')
        if members.intersection(row['refs']):
            raise ValueError(path + '.refs: footprint belongs to multiple groups')
        names.add(row['name'])
        members.update(row['refs'])
        _notes(row, path)
