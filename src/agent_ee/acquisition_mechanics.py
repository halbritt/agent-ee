"""The captured acquisition body/access recipe, bound once to original native owners."""
from decimal import Decimal
from pathlib import Path
import hashlib

from . import acquisition_source_supplement
from .catalog import strict_json
from .mechanical_reservations import _native_owner, _current_owner, _sha
from .storage import canonical, digest

SCHEMA = 'agent-ee.acquisition-mechanics/1'
BINDING = 'agent-ee.acquisition-mechanics-binding/1'
SOURCES = ('spec.md', 'requirement-map.json', 'analysis/geometry-model.json', 'analysis/geometry_model.py')
MODEL = '0badae526e172bd688bc634e1fb2dec040fbca80dab15641bb0ff7e83d7f23f4'
CHECKER = 'fb225768d6812abf6b116da31e029b940ca64ccf1427f9c0ef7b04814cf10294'
CONNECTORS = ('J1', 'J2', 'J3')


def declaration(root):
    root = Path(root)
    sources = {rel: digest(root / rel) for rel in SOURCES}
    if sources['analysis/geometry-model.json'] == acquisition_source_supplement.CORRECTED_MODEL:
        sources.update(acquisition_source_supplement.geometry_sources())
    return {'schema': SCHEMA, 'sources': sources}


def load(path):
    path = Path(path)
    document = strict_json(path.read_bytes())
    if (not isinstance(document, dict) or set(document) != {'schema', 'sources'}
            or document['schema'] != SCHEMA or not isinstance(document['sources'], dict)
            or not set(SOURCES) <= set(document['sources'])):
        raise ValueError('unsupported acquisition mechanics recipe')
    _version(document)
    raw = {}
    for rel, expected in document['sources'].items():
        source = path.parent / rel
        if any(p.is_symlink() for p in (source, *source.parents)) or not source.is_file():
            raise ValueError('acquisition mechanics source must be a regular captured file: ' + rel)
        raw[rel] = source.read_bytes()
        if hashlib.sha256(raw[rel]).hexdigest() != expected:
            raise ValueError('acquisition mechanics source changed: ' + rel)
    requirements = strict_json(raw['requirement-map.json'])
    from .typed_overlay import _check_pool
    try:
        pool = _check_pool(requirements)
        limits = [pool['engineering-geometry']['params']['limits']]
    except (KeyError, TypeError) as exc:
        raise ValueError('acquisition mechanics requirements are missing or malformed') from exc
    expected_limits = (('body_gap', 'min', .2, 'mm'), ('body_overlap', 'max', 0, 'mm2'),
                       ('body_outside', 'max', 0, 'mm2'), ('access_obstruction', 'max', 1e-8, 'mm2'))
    if (len(limits) != 1 or not isinstance(limits[0], dict) or any(
            not isinstance(limits[0].get(key), dict) or limits[0][key].get(bound) != value
            or limits[0][key].get('unit') != unit
            or limits[0][key].get('condition') != 'nominal25C-reviewed-acquisition'
            for key, bound, value, unit in expected_limits)):
        raise ValueError('acquisition mechanics requirements differ from the captured recipe')
    return {'declaration': document, 'model': strict_json(raw['analysis/geometry-model.json']),
            'model_raw': raw['analysis/geometry-model.json'].decode('utf-8')}


def _identity(binding):
    return hashlib.sha256(canonical({k: v for k, v in binding.items() if k != 'binding_sha256'})).hexdigest()


def _version(document):
    sources = document['sources']
    model = sources['analysis/geometry-model.json']
    extra = acquisition_source_supplement.geometry_sources() if model == acquisition_source_supplement.CORRECTED_MODEL else {}
    if (model not in (MODEL, acquisition_source_supplement.CORRECTED_MODEL)
            or sources['analysis/geometry_model.py'] != CHECKER
            or set(sources) != set(SOURCES) | set(extra)
            or any(sources.get(name) != pin for name, pin in extra.items())):
        raise ValueError('unsupported acquisition model/checker revision or source custody')


def _recipe(recipe):
    _version(recipe['declaration'])
    if (hashlib.sha256(recipe['model_raw'].encode()).hexdigest()
            != recipe['declaration']['sources']['analysis/geometry-model.json']
            or strict_json(recipe['model_raw'].encode()) != recipe['model']
            or recipe['declaration']['schema'] != SCHEMA):
        raise ValueError('acquisition mechanics recipe changed')


def bind(recipe, capture):
    _recipe(recipe)
    _sha(capture.get('board_sha256'), 'acquisition original board')
    owners = [_native_owner(fp) for fp in capture['footprints']]
    if any(not o['pads'] for o in owners):
        raise ValueError('acquisition native owner pad coverage missing')
    refs = [o['ref'] for o in owners]
    if (len(set(refs)) != len(refs) or len({o['uuid'] for o in owners}) != len(owners)
            or set(refs) != set(recipe['model']['packages'])):
        raise ValueError('acquisition model/native owner coverage differs')
    native = {fp['ref']: fp for fp in capture['footprints']}
    for owner in owners:
        if owner['side'] != 'F' or owner['rotation_deg'] % 90:
            raise ValueError('acquisition model requires front orthogonal owners')
    if any(native[ref].get('locked') is not True for ref in CONNECTORS):
        raise ValueError('acquisition connector anchors must be natively locked')
    binding = {'schema': BINDING, 'original_board_sha256': capture['board_sha256'],
               'recipe': recipe, 'owners': owners,
               'fixed': [fp['uuid'] for fp in capture['footprints'] if fp['locked']]}
    binding['binding_sha256'] = _identity(binding)
    return binding


def attach(description, binding, capture):
    """Verify current native identity against the original, never rebaseline a saved board."""
    if binding.get('schema') != BINDING or binding.get('binding_sha256') != _identity(binding):
        raise ValueError('acquisition mechanics binding changed')
    _recipe(binding['recipe'])
    current = {fp['uuid']: fp for fp in capture['footprints']}
    if len(current) != len(capture['footprints']) or set(current) != {o['uuid'] for o in binding['owners']}:
        raise ValueError('acquisition mechanics owner UUID coverage changed')
    by_uuid = {fp['uuid']: fp for fp in description['footprints']}
    if set(by_uuid) != set(current):
        raise ValueError('acquisition description owner coverage changed')
    for original in binding['owners']:
        owner = _current_owner(original, current[original['uuid']])
        if owner['side'] != 'F' or owner['rotation_deg'] % 90:
            raise ValueError('acquisition model requires front orthogonal owners')
        if original['uuid'] in binding['fixed'] and (not current[original['uuid']]['locked'] or any(
                owner[k] != original[k] for k in ('position_nm', 'rotation_deg', 'side'))):
            raise ValueError('acquisition original fixed anchor changed')
        model = binding['recipe']['model']['packages'][owner['ref']]
        w, h, _ = [Decimal(str(v)) for v in model['size_mm']]
        x, y = [Decimal(str(v)) for v in model['offset_mm']]
        local = [(x-w/2)*1000000, (y-h/2)*1000000, (x+w/2)*1000000, (y+h/2)*1000000]
        if any(v != int(v) for v in local) or w <= 0 or h <= 0:
            raise ValueError('unsupported acquisition body resolution')
        row = by_uuid[owner['uuid']]
        if any(row[k] != owner[k] for k in ('ref', 'side', 'position_nm', 'rotation_deg')):
            raise ValueError('acquisition native description pose/owner changed')
        row['source_body_local_nm'] = [int(v) for v in local]
        row['access_local_nm'] = ([int(local[0])-500000, int(local[1])-500000,
                                   int(local[2])+500000, int(local[3])+500000]
                                  if owner['ref'] in CONNECTORS else None)
    description['mechanics'] = {'schema': SCHEMA, 'binding_sha256': binding['binding_sha256'],
        'original_board_sha256': binding['original_board_sha256'],
        'sources': binding['recipe']['declaration']['sources'],
        'model_raw': binding['recipe']['model_raw'],
        'limits': binding['recipe']['model']['limits']}
    return description
