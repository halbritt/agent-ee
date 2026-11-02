"""Declared body/access mechanics, bound once to original native owners.

Known acquisition recipe revisions keep their exact source custody, checker and stated
limits. Any other model must state `"custody": "design-declared"`. It needs no checker or
requirement-map entry: it lists its own source files and access owners, and every declared
source is verified against its captured bytes when loaded. Declaring a model does not
qualify its numbers; checks and review do that. The acquisition recipe itself admits only
known revisions.
"""
from decimal import Decimal
from pathlib import Path
import hashlib

from .candidate import safe_relative
from .catalog import strict_json
from .mechanical_reservations import _native_owner, _current_owner, _sha
from .storage import canonical, digest

SCHEMA = 'agent-ee.acquisition-mechanics/1'
BINDING = 'agent-ee.acquisition-mechanics-binding/1'
SOURCES = ('spec.md', 'requirement-map.json', 'analysis/geometry-model.json', 'analysis/geometry_model.py')
MODEL = '0badae526e172bd688bc634e1fb2dec040fbca80dab15641bb0ff7e83d7f23f4'
CHECKER = 'fb225768d6812abf6b116da31e029b940ca64ccf1427f9c0ef7b04814cf10294'
MODEL_SOURCE = 'analysis/geometry-model.json'
CONNECTORS = ('J1', 'J2', 'J3')

_REVISION_RESOLVERS = []


def register_revision_resolver(resolver):
    """Register an external resolver for recipe-specific model hashes."""
    _REVISION_RESOLVERS.append(resolver)


def access_owners(model):
    """References whose bodies keep a mating and cable access column."""
    owners = model.get('access_owners', CONNECTORS)
    if not isinstance(owners, (list, tuple)) or not all(isinstance(ref, str) for ref in owners):
        raise ValueError('mechanics access owners must be a list of references')
    return tuple(owners)


def is_design_declared(item):
    """Whether an item (recipe, model, or binding) states design-declared custody."""
    if isinstance(item, dict):
        if item.get('custody') == 'design-declared':
            return True
        model = item.get('model')
        if isinstance(model, dict) and model.get('custody') == 'design-declared':
            return True
        recipe = item.get('recipe')
        if isinstance(recipe, dict):
            return is_design_declared(recipe)
        model_raw = item.get('model_raw')
        if isinstance(model_raw, str) and '"custody": "design-declared"' in model_raw:
            return True
    return False


def declaration(root):
    root = Path(root)
    known = _model_sources(digest(root / MODEL_SOURCE))
    if known is None:
        model = strict_json((root / MODEL_SOURCE).read_bytes())
        return {'schema': SCHEMA, 'sources': {rel: digest(root / rel) for rel in (MODEL_SOURCE, *_design_files(model))}}
    return {'schema': SCHEMA, 'sources': {**{rel: digest(root / rel) for rel in SOURCES}, **known}}


def _unverified_model(root):
    """Read only the custody marker's carrier; load() then authenticates these bytes."""
    path = root / MODEL_SOURCE
    return strict_json(path.read_bytes()) if path.is_file() and not path.is_symlink() else None


def _design_files(model):
    """The extra source files a design's own model names, confined to its source root."""
    files = model.get('source_files', []) if isinstance(model, dict) else None
    if not isinstance(files, list) or not all(isinstance(rel, str) for rel in files):
        raise ValueError('mechanics source_files must be a list of source-relative paths')
    return [str(safe_relative(rel)) for rel in files]


def _design_model(model, sources):
    """Refuse a malformed design model here, before any native capture or engine run."""
    if set(sources) != {MODEL_SOURCE, *_design_files(model)}:
        raise ValueError("mechanics declaration differs from its model's source_files")
    if 'access_owners' not in model:
        raise ValueError('a design-declared model must list its access_owners')
    packages = model.get('packages')
    if not isinstance(packages, dict) or not packages:
        raise ValueError('mechanics model must list its packages')
    for ref, package in packages.items():
        size, offset = package.get('size_mm') if isinstance(package, dict) else None, \
            package.get('offset_mm') if isinstance(package, dict) else None
        if (not isinstance(size, list) or len(size) != 3 or not isinstance(offset, list) or len(offset) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in size + offset)):
            raise ValueError('mechanics package needs size_mm [x, y, z] and offset_mm [x, y]: ' + ref)
    access_owners(model)


def known_revision(model_sha256):
    """Whether a model is one of the acquisition recipe's exact, source-pinned revisions."""
    return _model_sources(model_sha256) is not None


def _model_sources(model):
    for resolver in _REVISION_RESOLVERS:
        res = resolver(model)
        if res is not None:
            return res
    if model == MODEL:
        return {}
    try:
        import importlib
        acq_mechanics = importlib.import_module("agent_ee.acquisition.mechanics")
        return acq_mechanics._model_sources(model)
    except (ImportError, ModuleNotFoundError, AttributeError):
        pass
    return None


def identity(binding):
    """Calculate the canonical SHA-256 identity of a bound mechanics document."""
    return hashlib.sha256(canonical({k: v for k, v in binding.items() if k != 'binding_sha256'})).hexdigest()


_identity = identity


def load(path):
    path = Path(path)
    document = strict_json(path.read_bytes())
    if (not isinstance(document, dict) or set(document) != {'schema', 'sources'}
            or document['schema'] != SCHEMA or not isinstance(document['sources'], dict)
            or MODEL_SOURCE not in document['sources']):
        raise ValueError('unsupported acquisition mechanics recipe')
    known = known_revision(document['sources'][MODEL_SOURCE])
    _version(document, None if known else _unverified_model(path.parent))
    raw = {}
    for rel, expected in document['sources'].items():
        safe_relative(rel)
        source = path.parent / rel
        if any(p.is_symlink() for p in (source, *source.parents)) or not source.is_file():
            raise ValueError('acquisition mechanics source must be a regular captured file: ' + rel)
        raw[rel] = source.read_bytes()
        if hashlib.sha256(raw[rel]).hexdigest() != expected:
            raise ValueError('acquisition mechanics source changed: ' + rel)
    model = strict_json(raw[MODEL_SOURCE])
    if not known:
        _design_model(model, document['sources'])
        return {'declaration': document, 'model': model, 'model_raw': raw[MODEL_SOURCE].decode('utf-8')}
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
    return {'declaration': document, 'model': model, 'model_raw': raw[MODEL_SOURCE].decode('utf-8')}


def _version(document, parsed=None):
    sources = document['sources']
    model = sources[MODEL_SOURCE]
    extra = _model_sources(model)
    if extra is None and isinstance(parsed, dict) and parsed.get('custody') == 'design-declared':
        return
    checker = CHECKER
    try:
        import importlib
        acq_film = importlib.import_module("agent_ee.acquisition.film")
        checker = acq_film.CHECKERS.get(model, CHECKER)
    except (ImportError, ModuleNotFoundError, AttributeError):
        pass
    if (extra is None or sources.get('analysis/geometry_model.py') != checker
            or set(sources) != set(SOURCES) | set(extra)
            or any(sources.get(name) != pin for name, pin in extra.items())):
        raise ValueError('unsupported acquisition model/checker revision or source custody')


def _recipe(recipe):
    _version(recipe['declaration'], recipe['model'])
    if (hashlib.sha256(recipe['model_raw'].encode()).hexdigest()
            != recipe['declaration']['sources'][MODEL_SOURCE]
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
        raise ValueError('mechanics model/native owner coverage differs')
    native = {fp['ref']: fp for fp in capture['footprints']}
    for owner in owners:
        if owner['side'] != 'F' or owner['rotation_deg'] % 90:
            raise ValueError('acquisition model requires front orthogonal owners')
    if any(native.get(ref, {}).get('locked') is not True for ref in access_owners(recipe['model'])):
        raise ValueError('mechanics access owners must be natively locked')
    binding = {'schema': BINDING, 'original_board_sha256': capture['board_sha256'],
               'recipe': recipe, 'owners': owners,
               'fixed': [fp['uuid'] for fp in capture['footprints'] if fp['locked']]}
    binding['binding_sha256'] = identity(binding)
    return binding


def attach(description, binding, capture):
    """Verify current native identity against the original, never rebaseline a saved board."""
    if binding.get('schema') != BINDING or binding.get('binding_sha256') != identity(binding):
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
                                  if owner['ref'] in access_owners(binding['recipe']['model']) else None)
    description['mechanics'] = {'schema': SCHEMA, 'binding_sha256': binding['binding_sha256'],
        'original_board_sha256': binding['original_board_sha256'],
        'sources': binding['recipe']['declaration']['sources'],
        'model_raw': binding['recipe']['model_raw'],
        'limits': binding['recipe']['model'].get('limits', [])}
    return description
