"""Exact mechanical derivation for the generated ADP7104 development reference."""
from hashlib import sha256

from .reverse_blocking import DATA
from ..catalog import strict_json

_PINS = strict_json((DATA / 'geometry-sources.json').read_bytes())
BASE = _PINS['base_model']
MODEL = _PINS['model']
SOURCES = _PINS['sources']


def revise(raw):
    """Select the explicitly derived body/loop/current model from its exact base."""
    if sha256(raw).hexdigest() != BASE:
        raise ValueError('ADP geometry requires the exact retained acquisition base')
    model = (DATA / 'geometry-model.json').read_bytes()
    basis = (DATA / 'geometry-basis.json').read_bytes()
    if (sha256(model).hexdigest() != MODEL
            or sha256(basis).hexdigest() != SOURCES['power-reference/geometry-basis.json']):
        raise ValueError('ADP geometry derivation or source declaration changed')
    return model, basis
