"""Deterministic typed-input overlay over an immutable captured input set.

An overlay declaration names the exact base capture (spec, spec record and
requirement-map/1 hashes), authored obligation kinds per source span, and the
map/2 dispositions that replace map/1 entries. ``prepare`` copies the base
unchanged except for the regenerated ``requirement-map.json``, adds
``obligations.json`` and a manifest, and validates the typed accounting. It
never edits the base, never creates judgment records or witnesses, and never
runs a check.
"""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys

from . import requirement_map
from .candidate import safe_relative
from .hardware_interface import exact_keys, text
from .spec_record import sha256, verify
from .storage import json_bytes

SCHEMA = 'agent-ee.typed-overlay/1'
MANIFEST_SCHEMA = 'agent-ee.typed-overlay-manifest/1'
BASE_FILES = ('spec.md', 'spec-record.json', 'requirement-map.json')
SPAN_MODES = ('checks', 'limitation', 'bench', 'operational', 'judgment', 'context')


def load_overlay(raw):
    overlay = requirement_map.decode(raw)
    exact_keys(overlay, 'schema name base obligations mappings judgments derived_files added_inputs check_additions')
    if overlay['schema'] != SCHEMA:
        raise ValueError(f'expected {SCHEMA}')
    text(overlay['name'])
    exact_keys(overlay['base'], 'spec_sha256 spec_record_sha256 requirement_map_sha256')
    for field in ('obligations', 'mappings', 'judgments', 'derived_files'):
        if not isinstance(overlay[field], dict):
            raise ValueError(f'overlay {field} must be an object')
    for path, rule in overlay['derived_files'].items():
        safe_relative(text(path))
        exact_keys(rule, 'rebind')
        if not isinstance(rule['rebind'], dict) or not rule['rebind']:
            raise ValueError(f'{path}: rebind must map requirement ids to span ids')
    if not isinstance(overlay['added_inputs'], dict):
        raise ValueError('added_inputs must be an object')
    for at, entry in overlay['added_inputs'].items():
        safe_relative(text(at))
        exact_keys(entry, 'files')
        if not isinstance(entry['files'], dict) or not entry['files']:
            raise ValueError(f'added input {at} needs files with sha256 values')
        for name, digest in entry['files'].items():
            safe_relative(text(name))
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError(f'added input {at}/{name} needs a sha256')
    exact_keys(overlay['check_additions'], 'declare params')
    if not isinstance(overlay['check_additions']['declare'], list) or not isinstance(overlay['check_additions']['params'], dict):
        raise ValueError('check_additions needs a declare list and a params object')
    return overlay


def _span_locators(declaration, record):
    """Span id -> the exact baseline source locator the compiler will emit for it."""
    clauses = {c['id']: c for c in record['requirements'] + record['prose']}
    locators = {}
    for entry in declaration['requirements'] + declaration['prose']:
        for span in entry.get('spans', []):
            locator = {**clauses[entry['id']]['locator'], 'byte_start': span['byte_start'],
                       'byte_end': span['byte_end']}
            locators[span['mapping']['id']] = json.dumps(locator, sort_keys=True)
    return locators


def rebind_intervals(raw, rebind, locators):
    """Re-bind interval assertions from whole clauses to their executable spans.

    Only the ``requirement`` ids and their ``requirement_sources`` locators
    change; parameters, assertions, expressions and the source path/hash stay
    byte-for-byte as authored, so no operand or limit is altered.
    """
    document = requirement_map.decode(raw)
    sources = document.get('requirement_sources')
    if not isinstance(sources, dict):
        raise ValueError('rebind needs an interval document with requirement_sources')
    unknown = set(rebind) - set(sources)
    if unknown:
        raise ValueError('rebind names requirements the document does not cite: ' + ', '.join(sorted(unknown)))
    for new in rebind.values():
        if new not in locators:
            raise ValueError(f'rebind target {new} is not a span the derived map declares')
    for assertion in document['assertions']:
        assertion['requirement'] = rebind.get(assertion['requirement'], assertion['requirement'])
    for parameter in document['parameters'].values():
        if 'requirement' in parameter:
            parameter['requirement'] = rebind.get(parameter['requirement'], parameter['requirement'])
    document['requirement_sources'] = {
        rebind.get(rid, rid): ({**source, 'locator': locators[rebind[rid]]} if rid in rebind else source)
        for rid, source in sources.items()}
    return json_bytes(document)


def _check_pool(declaration):
    """Every map/1 check definition by id; conflicting duplicates refuse."""
    pool = {}

    def collect(entry):
        for spec in entry.get('checks', []):
            if spec['id'] in pool and pool[spec['id']] != spec:
                raise ValueError(f'base map declares check {spec["id"]} twice with different definitions')
            pool[spec['id']] = spec
    for entry in declaration['requirements'] + declaration['prose']:
        if 'spans' in entry:
            for span in entry['spans']:
                collect(span['mapping'])
        else:
            collect(entry)
    return pool


def _mapping(rid, span, pool):
    exact_keys(span, 'byte_start byte_end id mode ' + ' '.join(k for k in span if k not in
                                                                 ('byte_start', 'byte_end', 'id', 'mode')))
    mode = span['mode']
    if mode not in SPAN_MODES:
        raise ValueError(f'{rid}: unknown span mode {mode!r}')
    mapping = {'id': span['id']}
    if mode == 'checks':
        exact_keys(span, 'byte_start byte_end id mode checks')
        if not isinstance(span['checks'], list) or not span['checks']:
            raise ValueError(f'{rid}: checks span needs check ids')
        missing = [cid for cid in span['checks'] if cid not in pool]
        if missing:
            raise ValueError(f'{rid}: checks {missing} are not declared in the base map')
        mapping['checks'] = [copy.deepcopy(pool[cid]) for cid in span['checks']]
    elif mode == 'operational':
        exact_keys(span, 'byte_start byte_end id mode criterion')
        mapping['operational'] = {'criterion': span['criterion']}
    elif mode == 'judgment':
        exact_keys(span, 'byte_start byte_end id mode record')
        mapping['judgment'] = {'record': span['record']}
    elif mode == 'limitation':
        exact_keys(span, 'byte_start byte_end id mode reason')
        mapping['limitation'] = {'reason': span['reason']}
    elif mode == 'bench':
        exact_keys(span, 'byte_start byte_end id mode criterion procedure')
        mapping['bench'] = {'criterion': span['criterion'], 'procedure': span['procedure']}
    else:
        exact_keys(span, 'byte_start byte_end id mode reason')
        mapping['context'] = {'reason': span['reason']}
    return mapping


def _apply_check_additions(derived, pool, additions):
    """New check declarations join the pool; param additions apply to every declaration of that id."""
    for spec in additions['declare']:
        exact_keys(spec, 'id kind params stage')
        if spec['id'] in pool:
            raise ValueError(f'check_additions redeclares existing check {spec["id"]}')
        pool[spec['id']] = copy.deepcopy(spec)
    for cid, extra in additions['params'].items():
        if cid not in pool:
            raise ValueError(f'check_additions names unknown check {cid}')
        if not isinstance(extra, dict) or not extra:
            raise ValueError(f'check_additions params for {cid} must be a nonempty object')
        clash = set(extra) & set(pool[cid]['params'])
        if clash:
            raise ValueError(f'check_additions would overwrite params {sorted(clash)} of {cid}')
        pool[cid]['params'] = {**pool[cid]['params'], **extra}

        def update(entry):
            for spec in entry.get('checks', []):
                if spec['id'] == cid:
                    spec['params'] = {**spec['params'], **extra}
        for entry in derived['requirements'] + derived['prose']:
            if 'spans' in entry:
                for span in entry['spans']:
                    update(span['mapping'])
            else:
                update(entry)


def derive_map(declaration, overlay):
    """The map/2 declaration: base entries kept verbatim unless the overlay replaces them."""
    pool = _check_pool(declaration)
    derived = copy.deepcopy(declaration)
    derived['schema'] = requirement_map.SCHEMA_2
    _apply_check_additions(derived, pool, overlay['check_additions'])
    declared_inputs = {spec['at'] for spec in derived['scope'].get('inputs', [])}
    for at in overlay['added_inputs']:
        if at in declared_inputs:
            raise ValueError(f'added input {at} collides with a declared scope input')
        derived['scope'].setdefault('inputs', []).append({'path': at, 'at': at})
    known = {e['id'] for e in derived['requirements'] + derived['prose']}
    unknown = set(overlay['mappings']) - known
    if unknown:
        raise ValueError('overlay maps unknown requirement ids: ' + ', '.join(sorted(unknown)))
    for section in ('requirements', 'prose'):
        for index, entry in enumerate(derived[section]):
            replacement = overlay['mappings'].get(entry['id'])
            if replacement is None:
                continue
            if replacement == {'keep': True}:
                continue
            exact_keys(replacement, 'spans')
            if not isinstance(replacement['spans'], list) or not replacement['spans']:
                raise ValueError(f'{entry["id"]}: overlay spans must be nonempty')
            spans = [{'byte_start': s['byte_start'], 'byte_end': s['byte_end'],
                      'mapping': _mapping(entry['id'], s, pool)} for s in replacement['spans']]
            derived[section][index] = {'id': entry['id'], 'spans': spans}
    return derived


def derive_obligations(record, spec_raw, overlay):
    clauses = []
    for source in record['requirements'] + record['prose']:
        rid = source['id']
        if rid not in overlay['obligations']:
            raise ValueError(f'overlay declares no obligation for captured clause {rid}')
        spans = []
        for span in overlay['obligations'][rid]:
            exact_keys(span, 'byte_start byte_end obligation')
            start, end = span['byte_start'], span['byte_end']
            spans.append({'byte_start': start, 'byte_end': end,
                          'text_sha256': sha256(spec_raw[start:end]), 'obligation': span['obligation']})
        clauses.append({'id': rid, 'spans': spans})
    extra = set(overlay['obligations']) - {c['id'] for c in clauses}
    if extra:
        raise ValueError('overlay obligations name unknown clauses: ' + ', '.join(sorted(extra)))
    return {'schema': requirement_map.OBLIGATIONS_SCHEMA, 'spec_sha256': sha256(spec_raw), 'clauses': clauses}


def _inventory(root):
    files = {}
    for path in sorted(p for p in Path(root).rglob('*') if not p.is_dir()):
        if path.is_symlink():
            raise ValueError(f'base contains a link: {path}')
        files[path.relative_to(root).as_posix()] = sha256(path.read_bytes())
    return files


def _copy_added_inputs(overlay, sources, staging):
    """Copy declared added inputs from their named source directories, verifying every hash."""
    added = []
    missing = set(overlay['added_inputs']) - set(sources)
    if missing:
        raise ValueError('no source directory given for added inputs: ' + ', '.join(sorted(missing)))
    extra = set(sources) - set(overlay['added_inputs'])
    if extra:
        raise ValueError('source directories for undeclared added inputs: ' + ', '.join(sorted(extra)))
    for at, entry in overlay['added_inputs'].items():
        root = Path(sources[at]).resolve()
        for name, digest in entry['files'].items():
            source = root / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f'added input {at}/{name} is not an ordinary file under {root}')
            raw = source.read_bytes()
            if sha256(raw) != digest:
                raise ValueError(f'added input {at}/{name} differs from the declared sha256')
            target = staging / at / name
            if target.exists():
                raise ValueError(f'added input {at}/{name} would overwrite a base file')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            added.append(f'{at}/{name}')
    return sorted(added)


def prepare(base, overlay_path, out, *, compile_against=None, added_sources=None):
    """Write the derived typed input revision and validate its accounting."""
    base, out = Path(base).resolve(), Path(out).resolve()
    overlay_raw = Path(overlay_path).read_bytes()
    overlay = load_overlay(overlay_raw)
    added_sources = dict(added_sources or {})
    raw = {name: (base / name).read_bytes() for name in BASE_FILES}
    expected = {'spec.md': overlay['base']['spec_sha256'], 'spec-record.json': overlay['base']['spec_record_sha256'],
                'requirement-map.json': overlay['base']['requirement_map_sha256']}
    for name, digest in expected.items():
        if sha256(raw[name]) != digest:
            raise ValueError(f'{name}: base bytes differ from the overlay declaration')
    record = verify(raw['spec-record.json'], raw['spec.md'])
    declaration = requirement_map.map_declaration(raw['requirement-map.json'])
    if declaration['schema'] != requirement_map.SCHEMA:
        raise ValueError('overlay base must be a requirement-map/1')
    for path, brief in overlay['judgments'].items():
        safe_relative(text(path))
        exact_keys(brief, 'requirement span questions sources')
        if not brief['questions'] or not brief['sources']:
            raise ValueError(f'judgment brief {path} needs questions and sources')
    derived = derive_map(declaration, overlay)
    obligations = derive_obligations(record, raw['spec.md'], overlay)
    declared = {s['mapping']['judgment']['record'] for e in derived['requirements'] for s in e.get('spans', [])
                if 'judgment' in s['mapping']}
    if declared != set(overlay['judgments']):
        raise ValueError('judgment briefs must match the judgment records the map declares: '
                         f'{sorted(declared ^ set(overlay["judgments"]))}')
    base_files = _inventory(base)
    if out.exists():
        raise ValueError(f'{out} exists')
    staging = out.with_name(out.name + '.staging')
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(base, staging, symlinks=False)
    map_bytes, obligation_bytes = json_bytes(derived), json_bytes(obligations)
    (staging / 'requirement-map.json').write_bytes(map_bytes)
    (staging / 'obligations.json').write_bytes(obligation_bytes)
    added = ['obligations.json'] + _copy_added_inputs(overlay, added_sources, staging)
    locators = _span_locators(derived, record)
    changed = {'requirement-map.json': sha256(map_bytes)}
    for path, rule in overlay['derived_files'].items():
        target = staging / path
        if not target.is_file() or path in BASE_FILES:
            raise ValueError(f'derived file {path} is not an ordinary base file')
        derived_bytes = rebind_intervals(target.read_bytes(), rule['rebind'], locators)
        target.write_bytes(derived_bytes)
        changed[path] = sha256(derived_bytes)
    # Validate the typed accounting against the derived set before sealing it.
    files = _inventory(staging)
    bound = requirement_map.load_obligations(obligation_bytes, record, raw['spec.md'])
    scope, receipts = requirement_map.account_requirements(
        derived, record, raw['spec.md'], files=files, inputs=staging, obligations=bound)
    manifest = {'schema': MANIFEST_SCHEMA, 'name': overlay['name'], 'overlay_sha256': sha256(overlay_raw),
                'base': {'files': len(base_files), 'inventory_sha256': sha256(json_bytes(base_files)), **expected},
                'derived': {'requirement_map_sha256': sha256(map_bytes), 'obligations_sha256': sha256(obligation_bytes),
                            'unchanged_files': sorted(n for n, d in files.items() if base_files.get(n) == d),
                            'changed_files': {path: {'base_sha256': base_files[path], 'derived_sha256': digest}
                                              for path, digest in sorted(changed.items())},
                            'added_files': added,
                            'added_inputs': {at: {'files': entry['files'], 'source': str(Path(added_sources[at]).resolve())}
                                             for at, entry in overlay['added_inputs'].items()},
                            'check_additions': overlay['check_additions']},
                'accounting': {'rows': len(receipts),
                               'modes': {m: sum(r['mode'] == m for r in receipts) for m in SPAN_MODES},
                               'obligations': {k: sum(r.get('obligation') == k for r in receipts)
                                               for k in requirement_map.OBLIGATION_KINDS},
                               'judgment_records_declared': sorted(declared), 'judgment_records_present': [],
                               'limitations': scope['limitations']}}
    (staging / 'overlay-manifest.json').write_bytes(json_bytes(manifest))
    staging.rename(out)
    result = {'out': str(out), 'manifest': manifest}
    if compile_against is not None:
        result['receipt'] = compile_typed(out, Path(compile_against).resolve())
    return result


def compile_typed(derived, candidate):
    """Compile the derived declaration as the scaffold stage would, against a native candidate.

    The compile root is the retained scaffold candidate with the derived input
    files laid over it (what a scaffold stage run from the derived inputs would
    contain); the candidate itself and its provenance are left untouched.
    """
    manifest = json.loads((derived / 'overlay-manifest.json').read_bytes())
    root = derived.with_name(derived.name + '.compile')
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(candidate, root, symlinks=False)
    provenance = root / requirement_map.PROVENANCE
    if provenance.exists():
        shutil.rmtree(provenance)
    overlaid = list(manifest['derived']['changed_files']) + manifest['derived']['added_files']
    for name in overlaid:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(derived / name, target)
    files = _inventory(root)
    scope, receipt = requirement_map.compile_map(
        (derived / 'requirement-map.json').read_bytes(), (derived / 'spec-record.json').read_bytes(),
        (derived / 'spec.md').read_bytes(), files=files, inputs=root,
        obligations_raw=(derived / 'obligations.json').read_bytes())
    receipt['compile_root'] = {'candidate': str(candidate), 'overlaid': sorted(overlaid),
                               'inventory_sha256': sha256(json_bytes(files))}
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(prog='agent_ee.typed_overlay')
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('prepare', help='derive a typed input revision from an immutable base')
    command.add_argument('base', type=Path)
    command.add_argument('overlay', type=Path)
    command.add_argument('--out', type=Path, required=True)
    command.add_argument('--compile-against', type=Path, help='native scaffold candidate for a typed receipt')
    command.add_argument('--receipt', type=Path, help='where to write the typed receipt')
    command.add_argument('--added-input', action='append', default=[], metavar='AT=DIR',
                         help='source directory for a declared added input (repeatable)')
    args = parser.parse_args(argv)
    sources = {}
    for item in args.added_input:
        at, _, directory = item.partition('=')
        if not at or not directory:
            parser.error('--added-input needs AT=DIR')
        sources[at] = directory
    try:
        result = prepare(args.base, args.overlay, args.out, compile_against=args.compile_against,
                         added_sources=sources)
    except requirement_map.Refused as exc:
        json.dump({'outcome': 'refused', 'findings': exc.findings}, sys.stdout, indent=2, sort_keys=True)
        print()
        return 1
    except (ValueError, OSError) as exc:
        parser.exit(2, f'typed overlay not prepared: {exc}\n')
    if args.receipt is not None and 'receipt' in result:
        args.receipt.write_bytes(json_bytes(result['receipt']))
    summary = {'outcome': 'prepared', 'out': result['out'], 'manifest': result['manifest']['derived'],
               'accounting': {k: v for k, v in result['manifest']['accounting'].items() if k != 'limitations'}}
    if 'receipt' in result:
        summary['receipt'] = {k: result['receipt'][k] for k in ('schema', 'scope_sha256', 'baseline_id',
                                                                  'coverage_id', 'obligations_sha256')}
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
