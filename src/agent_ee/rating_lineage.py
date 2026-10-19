"""Coordinator rating provenance for campaigns that run binding-fill --ratings (AGENTEE-256).

A sealed scope cannot name a binding-fill receipt that does not exist yet, and
assembly_readiness (AGENTEE-242) stays unknown for AGENT_EE_Ratings fields without
one. A campaign opts in with ``rating_provenance``, a candidate-relative capture
directory the coordinator alone owns. When an implementation adopts a binding-fill
step with ``--ratings``, the coordinator copies that step's retained ``receipt.json``
and ``run/out/binding-fill.json`` into the directory of the next candidate, pins
``receipt_sha256`` from the copied bytes and declares ``rating_provenance`` on every
assembly_readiness check of the derived proposal scope. Later candidates inherit the
declaration and bytes until another adopted binding-fill replaces both.

Campaign verification re-derives the declaration each run is owed from anchored
receipts and refuses any other: a stale or foreign receipt, edited bytes or a hash
that is not the coordinator's. The sealed baseline remains the campaign authority:
a candidate's baseline with its coordinator declaration removed must equal it.
"""
import copy
import hashlib
from pathlib import Path
import re

from .storage import digest, read_json

RECEIPT = 'binding-fill.receipt.json'
REPORT = 'binding-fill.json'
ARTIFACT = 'out/binding-fill.json'
KIND = 'assembly_readiness'


def directory(config):
    """The declared coordinator capture directory, or None when the campaign does not opt in."""
    name = config.get('rating_provenance')
    if name is None:
        return None
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        raise ValueError('rating_provenance must name one top-level candidate capture directory')
    return name


def rating_step(spec):
    return spec['step'] == 'binding-fill' and '--ratings' in spec['argv']


def declaration(name, receipt_sha256):
    return {'receipt': f'{name}/{RECEIPT}', 'receipt_sha256': receipt_sha256, 'report': f'{name}/{REPORT}'}


def declared(scope):
    """The single rating_provenance shared by the scope's assembly checks, or None."""
    values = [c.get('params', {}).get('rating_provenance') for c in scope['checks'] if c.get('kind') == KIND]
    if any(v != values[0] for v in values):
        raise ValueError('assembly_readiness checks declare different rating_provenance')
    return values[0] if values else None


def strip(scope):
    """The sealed form of a derived scope: its coordinator declaration removed."""
    scope = copy.deepcopy(scope)
    for check in scope['checks']:
        if check.get('kind') == KIND:
            check.get('params', {}).pop('rating_provenance', None)
    return scope


def validate_campaign(config, scope, steps):
    """Refuse an opt-in that cannot bind, or a sealed scope that already names provenance."""
    name = directory(config)
    if name is None:
        return
    checks = [c for c in scope['checks'] if c.get('kind') == KIND]
    if not checks:
        raise ValueError('rating_provenance requires an assembly_readiness check')
    if any('rating_provenance' in c.get('params', {}) for c in checks):
        raise ValueError('sealed scope cannot declare rating_provenance; the coordinator derives it '
                         'from the adopted binding-fill receipt')
    if not any(rating_step(s) for s in steps):
        raise ValueError('rating_provenance requires a declared binding-fill step with --ratings')
    if any(Path(item['at']).parts[0] == name for item in scope['inputs']):
        raise ValueError(f'rating_provenance directory {name} collides with captured inputs')


def adopted(root, state, number):
    """(receipt directory, anchored receipt sha256, receipt) of implementation ``number``'s
    last adopted binding-fill --ratings step, or None when none ran."""
    from .stage_done import ADOPTABLE, _receipts
    from .stage_receipts import validate_receipt
    work = Path(root) / 'implementations' / str(number)
    found = None
    for k, sha, receipt in _receipts(root, state, str(number)):
        if rating_step(receipt['spec']) and receipt['status'] in ADOPTABLE:
            found = k, sha
    if found is None:
        return None
    k, sha = found
    link = read_json(work / 'stage-receipts.json')['receipts'][k - 1]
    path = work / link['path']
    source = read_json(Path(state['history'][number - 1]['run']) / 'candidate.json')
    receipt = validate_receipt(path.parent, binding={'implementation': number,
                               'source_candidate_id': source['candidate_id'], 'receipt_index': k})
    if digest(path) != sha:
        raise ValueError('binding-fill receipt changed after indexing')
    report = receipt['artifacts'].get(ARTIFACT)
    if report is None or digest(path.parent / 'run' / ARTIFACT) != report:
        raise ValueError('binding-fill report is not the receipted artifact')
    return path.parent, sha, receipt


def expected_files(name, sha, receipt):
    return {f'{name}/{RECEIPT}': sha, f'{name}/{REPORT}': receipt['artifacts'][ARTIFACT]}


def proposal_scope(scope, design, work, root, state, config):
    """The derived proposal scope of one implementation: the delivered design, plus the
    coordinator capture of a newly adopted binding-fill --ratings receipt when one ran."""
    scope = copy.deepcopy(scope)
    entries = sorted(Path(design).iterdir())
    name = directory(config)
    found = None if name is None else adopted(root, state, int(Path(work).name))
    if found is None:
        # Inherited provenance travels with the source capture; the writer cannot edit it.
        scope['inputs'] = [{'path': str(p), 'at': p.name} for p in entries]
        return scope
    receipt_dir, sha, receipt = found
    target = Path(work) / 'rating-provenance' / name
    target.mkdir(parents=True)
    data = {RECEIPT: (receipt_dir / 'receipt.json').read_bytes(), REPORT: (receipt_dir / 'run' / ARTIFACT).read_bytes()}
    # The pin is computed here from the adopted bytes, never read from a tool or writer field.
    pinned = hashlib.sha256(data[RECEIPT]).hexdigest()
    if {f'{name}/{k}': hashlib.sha256(v).hexdigest() for k, v in data.items()} != expected_files(name, sha, receipt):
        raise ValueError('binding-fill receipt or report changed before capture')
    for key, value in data.items():
        (target / key).write_bytes(value)
    if digest(target / RECEIPT) != pinned:
        raise ValueError('captured binding-fill receipt differs from the adopted bytes')
    scope['inputs'] = [{'path': str(p), 'at': p.name} for p in entries if p.name != name]
    scope['inputs'].append({'path': str(target), 'at': name})
    for check in scope['checks']:
        if check.get('kind') == KIND:
            check.setdefault('params', {})['rating_provenance'] = declaration(name, pinned)
    return scope


def delivered_view(config, captured, delivered):
    """A captured proposal's files with the coordinator capture replaced by the delivered design's."""
    name = directory(config)
    if name is None:
        return captured
    view = {rel: sha for rel, sha in captured.items() if rel.split('/', 1)[0] != name}
    view.update({rel: sha for rel, sha in delivered.items() if rel.split('/', 1)[0] == name})
    return dict(sorted(view.items()))


def verify_candidate(root, state, config, number, manifest):
    """Refuse run ``number`` unless its rating provenance is what the coordinator owes it.

    ``state['history']`` must hold the verified runs before it. Run 1 is owed none. A run
    captured from implementation ``number - 1``'s proposal is owed that implementation's
    adopted binding-fill --ratings receipt when one ran; otherwise every run inherits the
    previous run's declaration and capture bytes exactly."""
    name = directory(config)
    if name is None:
        return
    files = {rel: sha for rel, sha in manifest['files'].items() if rel.split('/', 1)[0] == name}
    if number == 1:
        expected, owed = None, {}
    else:
        previous = read_json(Path(state['history'][number - 2]['run']) / 'candidate.json')
        work = Path(root) / 'implementations' / str(number - 1)
        found = None
        if manifest.get('scope_path') == str(work / 'proposal.scope.json'):
            found = adopted(root, state, number - 1)
        if found is None:
            expected = declared(previous['scope'])
            owed = {rel: sha for rel, sha in previous['files'].items() if rel.split('/', 1)[0] == name}
        else:
            _, sha, receipt = found
            expected, owed = declaration(name, sha), expected_files(name, sha, receipt)
    if declared(manifest['scope']) != expected or files != owed:
        raise ValueError('candidate rating_provenance is not the coordinator binding of the adopted '
                         'binding-fill receipt of its lineage')


def sealed_baseline(run, config):
    """The candidate's baseline as the campaign compares it: without its coordinator declaration."""
    from .baseline import compile_baseline, load_baseline
    baseline = load_baseline(run)
    if directory(config) is None:
        return baseline
    manifest = read_json(Path(run) / 'candidate.json')
    sealed = strip(manifest['scope'])
    if sealed == manifest['scope']:
        return baseline
    return compile_baseline(sealed, manifest['files'], Path(run) / 'inputs')
