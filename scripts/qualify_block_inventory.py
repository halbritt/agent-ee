#!/usr/bin/env python3
"""Host-side qualification of a block inventory against its pinned source commit.

Captures the inventory's pinned commit twice, requires byte-identical entries,
requires every planned block to be an unverified entry with no runs, and replays
each adaptation fixture: without straps it must give exactly the expected
differences, with straps none. With an expected capture (by default the
committed fome-mc capture beside the committed inventory) the first capture must
also equal it byte for byte. Captures copy design files, so the output directory
must lie outside any Git work tree or be ignored by it.

Defaults qualify the committed fome-mc inventory (fixtures/fome-mc/inventory,
owner decision of 2026-10-18); each is overridable:

    AGENT_EE_FOME_MC_CAPTURE   private override directory holding block-inventory.json
                               and adaptation fixtures; captures go to its captures/
    AGENT_EE_FOME_MC_CHECKOUT  source repository (default ~/git/fome-mc); only its
                               Git objects at the pinned commit are read

    PYTHONPATH=src python3 scripts/qualify_block_inventory.py

This is a manual step, not part of the discovered test suite: it needs the
source repository and the host's KiCad. Exit 0 when qualified, 1 when a
qualification check fails, 2 when refused.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from agent_ee.block_inventory import (ADAPTATION_FIXTURE, adapt_connectivity, capture_inventory,
                                      compare_connectivity, validate_adaptation_fixture,
                                      validate_inventory)
from agent_ee.reference_blocks import _read, validate_block
from agent_ee.storage import write_json

ROOT = Path(__file__).resolve().parents[1]


COMMITTED = ROOT / 'fixtures/fome-mc/inventory'


def inventory_directory():
    """The private override directory if set, else the committed fome-mc inventory."""
    override = os.environ.get('AGENT_EE_FOME_MC_CAPTURE')
    return Path(override) if override else COMMITTED


def require_private(path):
    """Refuse an output path that a Git work tree would track."""
    path = Path(path).absolute()
    existing = next(p for p in (path, *path.parents) if p.exists())
    if not existing.is_dir():
        raise ValueError(f'private capture output is under a non-directory: {existing}')
    inside = subprocess.run(['git', '-C', str(existing), 'rev-parse', '--is-inside-work-tree'],
                            capture_output=True, text=True)
    if inside.returncode or inside.stdout.strip() != 'true':
        return
    # The trailing slash names a directory, so directory-only patterns apply before it exists.
    ignored = subprocess.run(['git', '-C', str(existing), 'check-ignore', '-q', str(path) + '/'],
                             capture_output=True)
    if ignored.returncode:
        raise ValueError(f'private capture output would be tracked by a Git work tree: {path}')


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob('*')) if p.is_file()}


def fixtures(directory):
    """Adaptation fixtures stored beside the private inventory."""
    found = []
    for path in sorted(Path(directory).glob('*.json')):
        if _read(path).get('schema') == ADAPTATION_FIXTURE:
            found.append(path)
    return found


def qualify(inventory, repository, out, adaptations, library_repos, expect=None, repository_label=None):
    validate_inventory(inventory)
    documents = []
    for path in adaptations:
        document = _read(path)
        validate_adaptation_fixture(document)
        if document['design'] != inventory['design']['name']:
            raise ValueError(f'adaptation fixture {path} names another design')
        documents.append((str(path), document))
    if expect is not None and not Path(expect).is_dir():
        raise ValueError(f'expected capture is not a directory: {expect}')
    out = Path(out)
    require_private(out)
    if out.exists() or out.is_symlink():
        raise FileExistsError(str(out))
    out.mkdir(parents=True)
    try:
        first = capture_inventory(inventory, repository, out / 'first', library_repos=library_repos,
                                  repository_label=repository_label)
        second = capture_inventory(inventory, repository, out / 'second', library_repos=library_repos,
                                   repository_label=repository_label)
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    failures = []
    if first != second or tree(out / 'first') != tree(out / 'second'):
        failures.append('two captures of the pinned commit differ')
    expected = None
    if expect is not None:
        captured, committed = tree(out / 'first'), tree(expect)
        differing = sorted(p for p in set(captured) | set(committed) if captured.get(p) != committed.get(p))
        expected = dict(directory=str(expect), differing=differing)
        if differing:
            failures.append(f'capture differs from the expected capture {expect} in {len(differing)} files')
    planned = sorted(b['id'] for b in inventory['blocks'])
    if sorted(b['directory'] for b in first['blocks']) != planned:
        failures.append('captured blocks differ from the inventory blocks')
    statuses = {}
    for block in first['blocks']:
        report = validate_block(out / 'first' / block['directory'])
        statuses[block['directory']] = report['status']
        if report['status'] != 'unverified' or report['runs']:
            failures.append(f'{block["directory"]} is not an unverified entry without runs')
    replayed = []
    for path, document in documents:
        projections = []
        for name in (document['source'], document['target']):
            directory = out / 'first' / name
            record = _read(directory / 'block.json')
            projections.append(_read(directory / record['connectivity']))
        bare = dict(document['parameters'], straps=[])
        without = compare_connectivity(adapt_connectivity(projections[0], bare), projections[1])
        strapped = compare_connectivity(adapt_connectivity(projections[0], document['parameters']), projections[1])
        if without != document['expected_without_straps']:
            failures.append(f'{path}: differences without straps are not the expected ones')
        if strapped:
            failures.append(f'{path}: strapped adaptation still differs')
        replayed.append(dict(fixture=path, without_straps=without, with_straps=strapped))
    result = dict(schema='agent-ee.block-inventory-qualification/1', design=inventory['design']['name'],
                  commit=first['commit'], components=first['components'], blocks=statuses,
                  adaptations=replayed, expected=expected, failures=failures,
                  status='failed' if failures else 'qualified')
    write_json(out / 'qualification.json', result)
    return result


def main(argv=None):
    directory = inventory_directory()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--inventory', default=str(directory / 'block-inventory.json'))
    parser.add_argument('--repo', default=os.environ.get('AGENT_EE_FOME_MC_CHECKOUT',
                                                         str(Path.home() / 'git/fome-mc')))
    parser.add_argument('--out', help='new private output directory (default build/fome-mc-inventory/captures/'
                                      'COMMIT-N, or OVERRIDE/captures/COMMIT-N)')
    parser.add_argument('--expect', help='capture the first capture must equal (default: the capture/ '
                                         'directory beside the inventory, if any)')
    parser.add_argument('--repository-label', help='source repository name recorded in the entries '
                                                   '(default: the inventory design name)')
    parser.add_argument('--adaptation', action='append', metavar='FIXTURE',
                        help='adaptation fixture (default: every fixture beside the inventory)')
    parser.add_argument('--library-repo', action='append', default=[], metavar='NAME=PATH',
                        help='repository for a declared external library (default: sibling of --repo)')
    args = parser.parse_args(argv)
    try:
        inventory = _read(args.inventory)
        validate_inventory(inventory)
        repos = {}
        for value in args.library_repo:
            name, sep, path = value.partition('=')
            if not sep or not name or not path or name in repos:
                raise ValueError('library repository must be unique NAME=PATH')
            repos[name] = path
        if not args.library_repo:
            # Library tables name these as ${KIPRJMOD}/../NAME/, a sibling checkout.
            for name in inventory['design'].get('external_libraries', {}):
                sibling = Path(args.repo).absolute().parent / name
                if sibling.is_dir():
                    repos[name] = str(sibling)
        out = args.out
        if out is None:
            parent = Path(args.inventory).parent
            # The committed inventory's captures go to the ignored build/ tree, never beside it.
            home = ROOT / 'build/fome-mc-inventory' if parent.resolve() == COMMITTED.resolve() else parent
            captures = home / 'captures'
            stem = inventory['design']['commit'][:12]
            out = next(captures / f'{stem}-{n}' for n in range(1, 10000) if not (captures / f'{stem}-{n}').exists())
        adaptations = args.adaptation if args.adaptation is not None else fixtures(Path(args.inventory).parent)
        expect = args.expect
        if expect is None and (Path(args.inventory).parent / 'capture').is_dir():
            expect = Path(args.inventory).parent / 'capture'
        result = qualify(inventory, args.repo, out, adaptations, repos, expect=expect,
                         repository_label=args.repository_label or inventory['design']['name'])
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps(dict(schema='agent-ee.block-inventory-qualification/1', status='refused', error=str(exc))))
        return 2
    print(json.dumps(dict(result, out=str(out)), sort_keys=True))
    return 0 if result['status'] == 'qualified' else 1


if __name__ == '__main__':
    sys.exit(main())
