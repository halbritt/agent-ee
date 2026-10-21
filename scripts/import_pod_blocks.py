#!/usr/bin/env python3
"""Import the telemetry pod's iteration-3 hand-placed regulator clusters into blocks/ (AGENTEE-342).

Reads pinned objects from a local fome-mc Git object store only (no checkout, no
fetch): cluster-poses.json, the seed board, its native XML, the packing request and
the D-PL-1 locality declaration. The seed is probed natively with pcbnew in a
scratch directory. Every entry is search-derived and advisory (owner ruling
2026-10-21): the clusters were arranged against agent-chosen bounds and the pod
has not been fabricated or tested. Only numeric records and source identities are
written; no fome-mc file is copied.

    PYTHONPATH=src python3 scripts/import_pod_blocks.py --repository ~/git/fome-mc --root .
    PYTHONPATH=src python3 scripts/import_pod_blocks.py --repository ~/git/fome-mc --check
"""
import argparse
import gzip
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from agent_ee import block_library  # noqa: E402
from agent_ee.native_netlist import read_native_inventory, read_sheet_names  # noqa: E402
from agent_ee.reference_blocks import _git  # noqa: E402

REPOSITORY = 'https://github.com/halbritt/fome-mc'
COMMIT = '7a8baa137bb5072016c49b5b57c1e38bf0634cff'
ITERATION = 'telemetry-pod/expansion/placement-candidate/iteration-3/'
CLUSTERS = ITERATION + 'inputs/cluster-poses.json'
SEED = ITERATION + 'inputs/seed.kicad_pcb.gz'
REQUEST = ITERATION + 'inputs/request.json'
# inputs/input-hashes.txt names inputs/native.xml by SHA-256; at this commit those
# bytes are tracked as revised-native/native.xml.
NATIVE = 'telemetry-pod/expansion/revised-native/native.xml'
LOCALITY = 'telemetry-pod/expansion/electrical-locality/pad-obligations/electrical-locality.json'
# (cluster anchor, entry id, part)
ENTRIES = (('U5', 'pod-lmr36520-dda-u5', 'LMR36520-DDA'), ('U7', 'pod-lmr36520-dda-u7', 'LMR36520-DDA'),
           ('U15', 'pod-tps62162-dsg-u15', 'TPS62162-DSG'), ('U26', 'pod-lm2903-soic8-u26', 'LM2903-SOIC8'))


def citation(anchor):
    return (f'fome-mc {COMMIT} {CLUSTERS} cluster {anchor}: hand-placed by the pod lane against agent-chosen '
            'D-PL-1 locality bounds; the pod has not been fabricated or tested. Imported as search-derived, '
            'advisory (owner ruling 2026-10-21).')


def read(repository, path):
    return _git(repository, 'cat-file', 'blob', f'{COMMIT}:{path}')


def entries(repository, scratch):
    from agent_ee.placement_probe import extract
    scratch = Path(scratch)
    (scratch / 'seed.kicad_pcb').write_bytes(gzip.decompress(read(repository, SEED)))
    (scratch / 'native.xml').write_bytes(read(repository, NATIVE))
    capture = extract(scratch / 'seed.kicad_pcb')
    inventory = read_native_inventory(scratch / 'native.xml')
    names = read_sheet_names(scratch / 'native.xml')
    sha = hashlib.sha256((scratch / 'native.xml').read_bytes()).hexdigest()
    policy = json.loads(read(repository, REQUEST))['policy']
    policy['electrical_locality'] = json.loads(read(repository, LOCALITY))
    raw = read(repository, CLUSTERS)
    return [block_library.import_project_cluster(
        raw, anchor, capture, inventory, names, sha, policy, block_id=block_id, version=1, part=part,
        citation=citation(anchor), repository=REPOSITORY, commit=COMMIT, path=CLUSTERS)
        for anchor, block_id, part in ENTRIES]


def write(root, built):
    root = Path(root)
    catalogue = root / block_library.CATALOGUE_PATH
    catalogue.parent.mkdir(parents=True, exist_ok=True)
    if not catalogue.exists():
        catalogue.write_text(json.dumps({'schema': block_library.CATALOGUE, 'entries': []}, indent=1) + '\n')
    for entry in built:
        block_library.add_entry(root, entry)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--repository', required=True, type=Path, help='local fome-mc Git object store')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--root', type=Path, help='write a new blocks/ catalogue under this root')
    mode.add_argument('--check', action='store_true', help="compare a fresh import with this tree's blocks/")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='agent-ee-pod-blocks-') as tmp:
        scratch = Path(tmp) / 'scratch'
        scratch.mkdir()
        built = entries(args.repository, scratch)
        if args.root:
            write(args.root, built)
            print(json.dumps({'status': 'written', 'entries': [e['id'] for e in built]}))
            return 0
        # The committed catalogue may hold other entries; each pod entry file must be
        # byte-identical to a fresh import and catalogued with its hash.
        catalogued = {(e['id'], e['version']) for e in block_library.validate_library(ROOT)}
        differ = sorted(path for entry in built for path in [block_library.entry_path(entry['id'], entry['version'])]
                        if (entry['id'], entry['version']) not in catalogued or not (ROOT / path).is_file()
                        or (ROOT / path).read_bytes() != block_library.entry_bytes(entry))
        print(json.dumps({'status': 'differs' if differ else 'reproduced', 'differ': differ}))
        return 1 if differ else 0


if __name__ == '__main__':
    raise SystemExit(main())
