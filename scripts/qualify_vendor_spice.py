#!/usr/bin/env python3
"""Qualify one captured vendor SPICE op-amp model against the owned control circuit.

The vendor files stay where the owner keeps them, outside the repository. A
manifest binds each file to its SHA-256 and declares the subcircuit and the
explicit owned-symbol pin map (3 IN+, 2 IN-, 7 V+, 4 V-, 6 OUT); nothing is
inferred. Every scenario is a real active_spice run in the ordinary sandboxed
ngspice through capture, the native netlist and the evidence seal:

- control: unity-gain buffer of the owned 10 kohm / 100 nF RC must meet the
  owned fixture's bounds around the independent analytic response;
- seeded: the same circuit with 100 kohm must be a complete bound defect;
- missing_model: a second unmodeled populated part must be unsupported;
- invalid_formal: a pin mapped to a nonexistent formal must be unsupported;
- unsafe_directive: the vendor root with an appended control block must be
  refused before ngspice runs;
- solver_failure: a conflicting supply source must be a solver failure.

The live MODEL_DIR is read exactly once: each declared file is copied into a
private read-only snapshot, the copied bytes are verified against the manifest,
and the parse and every scenario are derived from that snapshot. Each scenario's
captured model bytes are then compared with the verified hashes (the unsafe
scenario with its declared appended block), so a source changed during the run
cannot be qualified under the manifest's hashes.

The model is qualified only if every scenario returns exactly its expected
status and binds the verified bytes. A closure the bounded dialect refuses is recorded as unqualified with
the refusal; it is never loosened to run. The receipt records hashes, observed
statuses and evidence seals, never vendor bytes, but the private output
directory retains copies of the model files and is never a publication artifact
(publication stays behind AGENTEE-118).

    PYTHONPATH=src python3 scripts/qualify_vendor_spice.py MANIFEST MODEL_DIR --out NEW_PRIVATE_DIR
"""
import argparse
import copy
import hashlib
import math
import os
import re
import stat
import shutil
import time
from pathlib import Path

from agent_ee.active_spice import capture_models, model_path
from agent_ee.baseline import freeze_evidence, verify_evidence
from agent_ee.candidate import capture, verify_snapshot
from agent_ee.checks import run_checks
from agent_ee.storage import digest, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures' / 'active-spice'
REGISTRY = FIXTURE / 'vendor-qualifications.json'
MANIFEST_SCHEMA = 'agent-ee.vendor-spice-manifest/1'
RECEIPT_SCHEMA = 'agent-ee.vendor-spice-receipt/1'
REGISTRY_SCHEMA = 'agent-ee.vendor-spice-qualifications/1'
SYMBOL_PINS = ('2', '3', '4', '6', '7')
UNSAFE_SUFFIX = b'\n.control\nshell true\n.endc\n'
SAFE_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
SHA256 = re.compile(r'[0-9a-f]{64}')
EXPECTED = {
    'control': ('ok', 'pass', None),
    'seeded': ('ok', 'defect', None),
    'missing_model': ('unsupported', 'unknown', 'explicit model'),
    'invalid_formal': ('unsupported', 'unknown', 'existing formal'),
    'unsafe_directive': ('unsupported', 'unknown', 'directive'),
    'solver_failure': ('failed', 'unknown', 'ngspice solver reported'),
}
# Unity-gain follower of the owned 10 kohm / 100 nF low-pass with a 0.1 V step.
ANALYTIC = {'out_1ms': 0.1 * (1 - math.exp(-1)),
            'gain_100hz': 1 / math.sqrt(1 + (2 * math.pi * 0.1) ** 2)}


def load_manifest(path):
    manifest = read_json(path)
    allowed = {'schema', 'id', 'vendor', 'part', 'source', 'model_files', 'subcircuit',
               'pin_map', 'supplies', 'dialect'}
    if not isinstance(manifest, dict) or set(manifest) - allowed or manifest.get('schema') != MANIFEST_SCHEMA:
        raise ValueError(f'manifest must be {MANIFEST_SCHEMA} with only {sorted(allowed)}')
    if not isinstance(manifest.get('id'), str) or not SAFE_ID.fullmatch(manifest['id']):
        raise ValueError('manifest id must be a lowercase safe identifier')
    for key in ('vendor', 'part', 'source', 'subcircuit'):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            raise ValueError(f'manifest {key} is required')
    files = manifest.get('model_files')
    if (not isinstance(files, dict) or not files or
            not all(isinstance(v, str) and SHA256.fullmatch(v) for v in files.values())):
        raise ValueError('model_files must map each relative model path to its lowercase SHA-256')
    for rel in files:
        model_path(rel)
    pins = manifest.get('pin_map')
    if not isinstance(pins, dict) or sorted(pins) != sorted(SYMBOL_PINS):
        raise ValueError(f'pin_map must name exactly the owned symbol pins {list(SYMBOL_PINS)}')
    supplies = manifest.setdefault('supplies', {'positive_v': 15, 'negative_v': -15})
    if (not isinstance(supplies, dict) or set(supplies) != {'positive_v', 'negative_v'} or
            not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in supplies.values()) or
            not supplies['negative_v'] < 0 < 0.2 < supplies['positive_v']):
        raise ValueError('supplies need finite negative_v < 0 and positive_v above the 0.1 V step')
    if manifest.setdefault('dialect', 'ngspice') not in ('ngspice', 'ngspice-ps'):
        raise ValueError('dialect must be ngspice or ngspice-ps')
    return manifest


def bind_model_dir(manifest, model_dir):
    """Refuse unless every declared file exists unlinked with exactly its declared hash."""
    model_dir = Path(model_dir).resolve()
    observed = {}
    for rel, expected in manifest['model_files'].items():
        path = model_dir
        for part in Path(rel).parts:
            path = path / part
            if path.is_symlink():
                raise ValueError(f'vendor model file is linked: {rel}')
        if not path.is_file():
            raise ValueError(f'vendor model file is missing: {rel}')
        observed[rel] = digest(path)
        if observed[rel] != expected:
            raise ValueError(f'vendor model file hash differs from manifest: {rel}')
    return observed


def snapshot_model_dir(manifest, model_dir, snapshot):
    """Copy each declared file once into a private snapshot and verify the copied bytes.

    The source is opened without following links, read once, and the hash of exactly the
    bytes written is compared with the manifest; a file changed, replaced, linked or
    missing while it is copied refuses. Nothing later reads the live directory."""
    model_dir = Path(model_dir).resolve()
    snapshot.mkdir(mode=0o700)
    observed = {}
    for rel, expected in manifest['model_files'].items():
        path = model_dir
        for part in Path(rel).parts:
            path = path / part
            if path.is_symlink():
                raise ValueError(f'vendor model file is linked: {rel}')
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise ValueError(f'vendor model file is missing: {rel}') from None
        except OSError as exc:
            raise ValueError(f'vendor model file is linked or unreadable: {rel}: {exc}') from None
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f'vendor model file is not a regular file: {rel}')
            data = stream.read()
            after = os.fstat(stream.fileno())
        current = os.lstat(path)
        identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        if identity(before) != identity(after) or identity(before) != identity(current):
            raise ValueError(f'vendor model file changed while it was snapshotted: {rel}')
        observed[rel] = hashlib.sha256(data).hexdigest()
        if observed[rel] != expected:
            raise ValueError(f'vendor model file hash differs from manifest: {rel}')
        target = snapshot / rel
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(target, 'xb') as handle:
            handle.write(data)
        target.chmod(0o400)
        if digest(target, follow_symlinks=False) != expected:
            raise ValueError(f'snapshot copy differs from manifest: {rel}')
    return observed


def base_scope(manifest, source):
    scope = read_json(FIXTURE / 'control.scope.json')
    scope['name'] = f'Vendor model qualification {manifest["id"]}'
    scope['scope'] = 'Owned control circuit around a captured vendor op-amp model; no publication.'
    scope['inputs'][0]['path'] = str(source)
    scope['inputs'][0]['include'] = ['active.kicad_sch', 'requirements.md', 'active.kicad_pcb',
                                     *(f'vendor/{rel}' for rel in manifest['model_files'])]
    params = scope['checks'][1]['params']
    params['dialect'] = manifest['dialect']
    params['model_files'] = [f'design/vendor/{rel}' for rel in manifest['model_files']]
    params['active_parts'] = {'U1': {'model_file': None, 'subcircuit': manifest['subcircuit'],
                                     'pin_map': dict(manifest['pin_map'])}}
    sources = {s['id']: s for s in params['sources']}
    sources['positive']['dc_v'] = manifest['supplies']['positive_v']
    sources['negative']['dc_v'] = manifest['supplies']['negative_v']
    return scope


def stage(observed, snapshot, directory):
    """Scenario source from the verified snapshot only; each copy is rechecked."""
    source = directory / 'source'
    source.mkdir(parents=True)
    for name in ('active.kicad_sch', 'requirements.md', 'active.kicad_pcb'):
        shutil.copyfile(FIXTURE / name, source / name)
    for rel, sha in observed.items():
        data = (snapshot / rel).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(f'private snapshot changed after verification: {rel}')
        (source / 'vendor' / rel).parent.mkdir(parents=True, exist_ok=True)
        (source / 'vendor' / rel).write_bytes(data)
    return source


def seed(source):
    for name in ('active.kicad_sch', 'active.kicad_pcb'):
        path = source / name
        text = path.read_text()
        if text.count('"10k"') != 1:
            raise ValueError(f'owned control fixture changed: {name} has no unique 10k value')
        path.write_text(text.replace('"10k"', '"100k"'))


def unmodeled(source):
    path = source / 'active.kicad_sch'
    path.write_text(path.read_text().replace('"Device:R"', '"agent:Unknown"').replace('"R_1_1"', '"Unknown_1_1"'))


def bind_capture(name, run, observed, snapshot, roots):
    """Compare the run's captured vendor bytes with the verified snapshot hashes.

    The unsafe scenario's roots must equal the verified bytes plus UNSAFE_SUFFIX
    exactly; every other file, and every file of every other scenario, must equal
    the verified bytes. The candidate snapshot itself is reverified after the run."""
    files = verify_snapshot(run)['files']
    expected = {}
    for rel, sha in observed.items():
        if name == 'unsafe_directive' and rel in roots:
            data = (snapshot / rel).read_bytes()
            if hashlib.sha256(data).hexdigest() != sha:
                raise ValueError(f'private snapshot changed after verification: {rel}')
            expected[rel] = hashlib.sha256(data + UNSAFE_SUFFIX).hexdigest()
        else:
            expected[rel] = sha
    captured = {rel: files.get(f'design/vendor/{rel}') for rel in observed}
    record = {'captured_sha256': captured, 'bound': captured == expected}
    if name == 'unsafe_directive':
        record['mutation'] = {'appended_sha256': hashlib.sha256(UNSAFE_SUFFIX).hexdigest(),
                              'roots': sorted(r for r in roots if r in observed)}
    return record


def scenario(name, manifest, observed, snapshot, root, model_file, roots, max_seconds):
    directory = root / name
    source = stage(observed, snapshot, directory)
    scope = base_scope(manifest, source)
    params = scope['checks'][1]['params']
    params['active_parts']['U1']['model_file'] = model_file
    if name == 'seeded':
        seed(source)
    elif name == 'missing_model':
        unmodeled(source)
    elif name == 'invalid_formal':
        params['active_parts']['U1']['pin_map']['3'] = 'AGENT_EE_NONEXISTENT_FORMAL'
    elif name == 'unsafe_directive':
        for rel in roots:
            with open(source / 'vendor' / rel, 'ab') as handle:
                handle.write(UNSAFE_SUFFIX)
    elif name == 'solver_failure':
        supply = next(s for s in params['sources'] if s['id'] == 'positive')
        params['sources'].append({**copy.deepcopy(supply), 'id': 'conflict', 'dc_v': supply['dc_v'] / 2})
    write_json(directory / 'scope.json', scope)
    run = directory / 'run'
    run.mkdir()
    capture(directory / 'scope.json', run)
    results = run_checks(run, time.monotonic() + max_seconds)
    freeze_evidence(run, results)
    verify_evidence(run, results, required=True)
    binding = bind_capture(name, run, observed, snapshot, roots)
    result = results[-1]
    execution, outcome, marker = EXPECTED[name]
    status = {'netlist': results[0]['execution'], 'execution': result['execution'],
              'outcome': result['outcome'], 'summary': result['summary']}
    matched = (binding['bound'] and results[0]['execution'] == 'ok' and (result['execution'], result['outcome']) == (execution, outcome)
               and (marker is None or marker in result['summary']))
    if name == 'unsafe_directive':
        # Refusal must precede any ngspice invocation, including the version probe.
        matched = matched and not (run / 'work/active/version').exists()
    record = {'expected': {'execution': execution, 'outcome': outcome, 'summary_contains': marker},
              'observed': status, 'matched': matched, 'model_binding': binding,
              'evidence_seal_sha256': digest(run / 'work/verification/evidence.json')}
    if 'metrics' in result['details']:
        record['metrics'] = {k: v['value'] for k, v in result['details']['metrics'].items()}
    if 'producer' in result['details']:
        record['ngspice_sha256'] = result['details']['producer']['sha256']
    for analysis in ('step', 'gain'):
        deck = run / f'work/active/{analysis}/deck.cir'
        if deck.is_file():
            record.setdefault('deck_sha256', {})[analysis] = digest(deck)
    return record


def qualify(manifest_path, model_dir, destination, max_seconds=300):
    manifest = load_manifest(manifest_path)
    destination = Path(destination).resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    snapshot = destination / 'snapshot'
    try:
        observed = snapshot_model_dir(manifest, model_dir, snapshot)
    except BaseException:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise
    receipt = {'schema': RECEIPT_SCHEMA, 'id': manifest['id'], 'vendor': manifest['vendor'],
               'part': manifest['part'], 'source': manifest['source'],
               'manifest_sha256': digest(Path(manifest_path)), 'model_files': observed,
               'subcircuit': manifest['subcircuit'], 'pin_map': manifest['pin_map'],
               'supplies': manifest['supplies'], 'dialect': manifest['dialect'],
               'analytic': ANALYTIC, 'scenarios': {}, 'qualified': False}
    try:
        models = capture_models(snapshot, observed, list(manifest['model_files']))
        header = models['subcircuits'].get(manifest['subcircuit'].lower())
        if header is None:
            raise ValueError(f'subcircuit {manifest["subcircuit"]} is not defined in the closure')
        if sorted(manifest['pin_map'].values(), key=str.lower) != sorted(header['formals'], key=str.lower):
            raise ValueError(f'pin_map formals {sorted(manifest["pin_map"].values())} differ from '
                             f'header formals {header["formals"]}')
    except (ValueError, UnicodeError, OSError) as exc:
        receipt['reason'] = f'captured closure refused by the bounded active-spice dialect: {exc}'
        write_json(destination / 'receipt.json', receipt)
        return receipt
    receipt['header'] = {'path': header['path'], 'line': header['line'], 'formals': header['formals']}
    model_file = f'design/vendor/{header["path"]}'
    for name in EXPECTED:
        receipt['scenarios'][name] = scenario(name, manifest, observed, snapshot, destination / 'scenarios',
                                              model_file, models['roots'], max_seconds)
    failed = [name for name, record in receipt['scenarios'].items() if not record['matched']]
    unbound = [name for name, record in receipt['scenarios'].items() if not record['model_binding']['bound']]
    receipt['qualified'] = not failed
    if unbound:
        receipt['reason'] = 'scenario captures do not bind the verified model bytes: ' + ', '.join(unbound)
    elif failed:
        receipt['reason'] = 'scenarios did not return their expected status: ' + ', '.join(failed)
    write_json(destination / 'receipt.json', receipt)
    return receipt


def validate_registry(registry):
    """Tracked record: hashes and receipts only; a qualified entry needs both."""
    if not isinstance(registry, dict) or registry.get('schema') != REGISTRY_SCHEMA:
        raise ValueError(f'registry must be {REGISTRY_SCHEMA}')
    if set(registry) != {'schema', 'publication', 'entries'} or not isinstance(registry['entries'], list):
        raise ValueError('registry has schema, publication and entries only')
    seen = set()
    for entry in registry['entries']:
        keys = {'id', 'vendor', 'part', 'status', 'model_files', 'receipt_sha256', 'reason', 'history'}
        if not isinstance(entry, dict) or set(entry) != keys:
            raise ValueError(f'registry entry needs exactly {sorted(keys)}')
        if not SAFE_ID.fullmatch(entry['id']) or entry['id'] in seen:
            raise ValueError(f'invalid or duplicate registry id {entry["id"]!r}')
        seen.add(entry['id'])
        files = entry['model_files']
        if not isinstance(files, dict) or not all(SHA256.fullmatch(str(v)) for v in files.values()):
            raise ValueError(f'{entry["id"]}: model_files must map paths to SHA-256')
        if entry['status'] == 'qualified':
            if not files or not SHA256.fullmatch(str(entry['receipt_sha256'])) or entry['reason'] is not None:
                raise ValueError(f'{entry["id"]}: qualified entries need file hashes and a receipt hash')
        elif entry['status'] == 'unqualified':
            if not isinstance(entry['reason'], str) or not entry['reason'].strip():
                raise ValueError(f'{entry["id"]}: unqualified entries need a reason')
        else:
            raise ValueError(f'{entry["id"]}: status must be qualified or unqualified')
    return registry


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('model_dir', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--max-seconds', type=float, default=300)
    args = parser.parse_args()
    receipt = qualify(args.manifest, args.model_dir, args.out, args.max_seconds)
    print(args.out / 'receipt.json')
    return 0 if receipt['qualified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
