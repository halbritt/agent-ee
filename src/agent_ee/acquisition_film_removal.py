"""Bind independently varied removal evidence without inheriting acceptance."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from . import acquisition_power_prepare
from .catalog import strict_json
from .storage import json_bytes

DATA = Path(__file__).resolve().parents[2] / 'fixtures/acquisition-film'
AT = 'analysis/film-removal/'
CONDITION = 'adp7104-film-25c-independent-removal-sensitivity'
CHECK = 'film-independent-removal'


def attach(files, root):
    declaration = strict_json((DATA / 'removal-evidence.json').read_bytes())
    for name in ('circuit', 'parts'):
        if sha256(files[name + '.json']).hexdigest() != declaration[name + '_sha256']:
            raise ValueError('removal evidence requires the exact generated ' + name)
    additions = acquisition_power_prepare._captured(root, declaration['files'])
    if set(additions) & set(files):
        raise ValueError('removal evidence collides with existing candidate inputs')
    matrix = strict_json(additions[AT + 'matrix-completion.json'])
    expected = sorted(declaration['cases'])
    if (matrix['execution'] != 'ok' or len(expected) != 8
            or sorted(row['case'] for row in matrix['completed']) != expected):
        raise ValueError('removal evidence requires all eight completed native cases')
    # Native completion is admission evidence. The fresh required check decides
    # whether the measured values satisfy the unchanged electrical limits.
    files.update(additions)
    files[AT + 'evidence-manifest.json'] = json_bytes(declaration)
    files[AT + 'replay.py'] = (DATA / 'removal_replay.py').read_bytes()
    analysis = Path(__file__).with_name('acquisition_power_analysis.py').read_bytes()
    if sha256(analysis).hexdigest() != declaration['power_analysis_sha256']:
        raise ValueError('removal measurement reader differs from the executed investigation')
    files[AT + 'power_analysis.py'] = analysis
    adapter = (DATA / 'power_adapter.py').read_bytes()
    old_condition = b'adp7104-film-25c-physical-open-sensitivity'
    if adapter.count(old_condition) != 1:
        raise ValueError('removal measurement adapter changed')
    files[AT + 'power_adapter.py'] = adapter.replace(old_condition, CONDITION.encode())
    document = 'power-reference/removal-bounds.md'
    basis = strict_json(files['power-reference/engineering-basis.json'])
    basis['independent_removal'] = dict(path=document, sha256=sha256(files[document]).hexdigest(),
        status='Independent capacitor sensitivities; fresh required check and engineering judgment remain necessary.')
    basis['sources'][document] = sha256(files[document]).hexdigest()
    files['power-reference/engineering-basis.json'] = json_bytes(basis)
    calculation = strict_json(files['engineering-calculations.json'])
    calculation['sources']['power-reference/engineering-basis.json'] = sha256(files['power-reference/engineering-basis.json']).hexdigest()
    calculation['sources'][document] = sha256(files[document]).hexdigest()
    files['engineering-calculations.json'] = json_bytes(calculation)
    files['OPERATING.md'] += (
        '\nIndependent capacitor removal sensitivities are retained in '
        'power-reference/removal-bounds.md and analysis/film-removal. '
        'The source-opening and host-release sequence above remains required. '
        'These are model observations, not physical validation or guaranteed product corners.\n').encode()


def check(original, files):
    result = deepcopy(original)
    result['id'] = CHECK
    params = result['params']
    params['adapter'] = AT + 'power_adapter.py'
    params['tool']['argv'][1] = '/candidate/' + AT + 'replay.py'
    params['tool']['inputs'] = ['circuit.json', 'parts.json'] + sorted(p for p in files if p.startswith(AT))
    for limit in params['limits'].values():
        limit['condition'] = CONDITION
    return result
