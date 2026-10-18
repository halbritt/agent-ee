#!/usr/bin/env python3
"""Check that docs/file-origins.json classifies every repository file.

The inventory records each file's origin, upstream license and publication
status. This check is structural: it proves every file matches exactly one most
specific rule, that no rule is stale, that files which look like third-party
material are not silently classified as agent-ee material, and that retained
captures and archives declare every origin their inputs came from. It does not
establish anyone's rights or read upstream licenses.

    python3 scripts/check_origins.py          # check; exit 1 on any error
    python3 scripts/check_origins.py --list   # per-file inventory as JSON
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = 'docs/file-origins.json'
SCHEMA = 'agent-ee.file-origins/1'
OWNED = {'AE', 'AE-RUN'}
PROPOSED = 'proposed-mit-pending-owner-approval'
UNRESOLVED = 'unresolved-owner-decision-required'
CONFIRMED = 'owner-confirmed'
STATUSES = (PROPOSED, UNRESOLVED, CONFIRMED)

# Mirrors .gitignore for checkouts without .git (for example the container image).
IGNORED_DIRS = {'.git', '__pycache__', '.venv', 'build', '.pytest_cache'}
IGNORED_EVIDENCE_DIRS = {'inputs', 'home', 'kicad-config'}
GITIGNORE_LINES = {'__pycache__/', '*.py[cod]', '.venv/', 'build/', '*.egg-info/', '.pytest_cache/',
                   'evidence/**/inputs/', 'evidence/**/home/', 'evidence/**/kicad-config/'}

RISKY_SUFFIXES = (
    '.pdf', '.html', '.htm', '.tmp', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.gz', '.zip', '.tar',
    '.kicad_sch', '.kicad_pcb', '.kicad_pro', '.kicad_prl', '.kicad_sym', '.kicad_mod', '.kicad_dru',
    '.lib', '.dcm', '.mod', '.sch', '.step', '.stp', '.wrl',
    '.drl', '.gbr', '.gbrjob', '.gbl', '.gbs', '.gbo', '.gtl', '.gts', '.gto', '.gm1', '.g1', '.g2', '.g3', '.g4',
    '.h', '.c', '.cpp', '.asc', '.gpg',
    'fp-lib-table', 'sym-lib-table',
)
CONTENT_MARKERS = {
    'KiCad design text': re.compile(rb'\((kicad_sch|kicad_pcb|kicad_symbol_lib)[\s)]|\(footprint "'),
    'netlist export': re.compile(rb'<export version='),
    'PDF header': re.compile(rb'%PDF-'),
    'HTML document': re.compile(rb'(?i)<!doctype html|<html[\s>]'),
    'PGP block': re.compile(rb'-----BEGIN PGP'),
    'rights notice': re.compile(rb'(?i)all rights reserved|copyright (\(c\)|\xc2\xa9)'),
    'license identifier': re.compile(rb'SPDX-License-Identifier'),
    'third-party project name': re.compile(rb'(?i)polygonus|mck1117|rusefi|samacsys'),
}


def tracked_files(root=ROOT):
    """Tracked files from Git, or a .gitignore-equivalent walk without .git."""
    root = Path(root)
    if (root / '.git').exists():
        out = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'], capture_output=True, check=True).stdout
        return sorted(p for p in out.decode().split('\0') if p and (root / p).is_file())
    found = []
    for directory, dirs, files in os.walk(root):
        rel = Path(directory).relative_to(root)
        evidence = rel.parts[:1] == ('evidence',)
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.endswith('.egg-info')
                   and not (evidence and d in IGNORED_EVIDENCE_DIRS)]
        for name in files:
            if not re.search(r'\.py[cod]$', name):
                found.append((rel / name).as_posix())
    return sorted(found)


def risky_name(path):
    return path.endswith(RISKY_SUFFIXES)


def content_markers(data):
    return sorted(label for label, pattern in CONTENT_MARKERS.items() if pattern.search(data))


def validate(inventory):
    errors = []
    if inventory.get('schema') != SCHEMA:
        errors.append(f'schema must be {SCHEMA}')
    origins, kinds = inventory.get('origins', {}), inventory.get('kinds', {})
    for key, origin in origins.items():
        status = origin.get('permission_status')
        if status not in STATUSES:
            errors.append(f'origin {key}: unknown permission_status {status!r}')
        if status == CONFIRMED and not str(origin.get('owner_record') or '').strip():
            errors.append(f'origin {key}: owner-confirmed requires owner_record')
        if key not in OWNED and status == PROPOSED:
            errors.append(f'origin {key}: third-party origin cannot be proposed under MIT')
        for field in ('name', 'upstream_license', 'owner_decision'):
            if not str(origin.get(field) or '').strip():
                errors.append(f'origin {key}: missing {field}')
        embedded = origin.get('embedded_origins', [])
        if not isinstance(embedded, list):
            errors.append(f'origin {key}: embedded_origins must be a list')
        else:
            errors.extend(f'origin {key}: unknown embedded origin {other!r}' for other in embedded
                          if other not in origins)
            if key in embedded:
                errors.append(f'origin {key}: embeds itself')
    for index, rule in enumerate(inventory.get('rules', [])):
        label = f'rule {index}'
        paths = rule.get('paths')
        if not isinstance(paths, list) or not paths:
            errors.append(f'{label}: paths must be a non-empty list')
            continue
        for path in paths:
            pure = PurePosixPath(path.rstrip('/') or '.')
            if not path or path.startswith('/') or '..' in pure.parts or pure.as_posix() != path.rstrip('/'):
                errors.append(f'{label}: path {path!r} is not a normalized relative path')
        if rule.get('kind') not in kinds:
            errors.append(f'{label}: unknown kind {rule.get("kind")!r}')
        ids = rule.get('origins')
        if not isinstance(ids, list) or not ids:
            errors.append(f'{label}: origins must be a non-empty list')
        else:
            errors.extend(f'{label}: unknown origin {key!r}' for key in ids if key not in origins)
        if 'suffixes' in rule and not (isinstance(rule['suffixes'], list) and rule['suffixes']):
            errors.append(f'{label}: suffixes must be a non-empty list')
        if 'reviewed' in rule:
            if not str(rule['reviewed']).strip():
                errors.append(f'{label}: reviewed needs a reason')
            if any(path.endswith('/') for path in paths) or 'suffixes' in rule:
                errors.append(f'{label}: reviewed rules must list exact paths')
    return errors


def matches(rule, path):
    """(specificity, rule path) of the best matching rule path, or None."""
    if 'suffixes' in rule and not path.endswith(tuple(rule['suffixes'])):
        return None
    best, filtered = None, int('suffixes' in rule)
    for candidate in rule['paths']:
        if candidate == path:
            score = (1, len(candidate), filtered)
        elif candidate.endswith('/') and path.startswith(candidate):
            score = (0, len(candidate), filtered)
        else:
            continue
        if best is None or score > best[0]:
            best = (score, candidate)
    return best


def classify(inventory, paths):
    """Map each path to (rule index, rule path); report unclassified, ambiguous and unused rule paths."""
    rules = inventory['rules']
    assigned, errors, used = {}, [], set()
    for path in paths:
        scored = [(found, index) for index, rule in enumerate(rules) if (found := matches(rule, path))]
        if not scored:
            errors.append(f'{path}: no rule classifies this file')
            continue
        top = max(found[0] for found, _ in scored)
        winners = [(index, found[1]) for found, index in scored if found[0] == top]
        if len(winners) > 1:
            errors.append(f'{path}: ambiguous between rules {[index for index, _ in winners]}')
            continue
        assigned[path] = winners[0][0]
        used.add(winners[0])
    errors += [f'rule {index}: {candidate} selects no file' for index, rule in enumerate(rules)
               for candidate in rule['paths'] if (index, candidate) not in used]
    return assigned, errors


def status(inventory, ids):
    """Unresolved if any origin is; else owner-confirmed if any origin is; MIT is proposed only when all are."""
    statuses = {inventory['origins'][key]['permission_status'] for key in ids}
    if UNRESOLVED in statuses:
        return UNRESOLVED
    return CONFIRMED if CONFIRMED in statuses else PROPOSED


def with_embedded(inventory, ids):
    """Declared origins plus every origin they embed, transitively.

    Upstream content carried inside a file keeps its own permission: a file whose
    owner-confirmed origin embeds an unresolved upstream is unresolved.
    """
    found, pending = set(), list(ids)
    while pending:
        key = pending.pop()
        if key not in found:
            found.add(key)
            pending.extend(inventory['origins'][key].get('embedded_origins', []))
    return sorted(found)


def capture_origins(inventory, manifest):
    """Origins of captured candidate inputs, from each origin's capture_sources."""
    found, errors = set(), []
    for captured, source in sorted(manifest.get('sources', {}).items()):
        hits = [(len(marker), key) for key, origin in inventory['origins'].items()
                for marker in origin.get('capture_sources', []) if marker in str(source)]
        if not hits:
            errors.append(f'captured {captured} from unrecognized source {source}')
            continue
        top = max(length for length, _ in hits)
        keys = {key for length, key in hits if length == top}
        if len(keys) > 1:
            errors.append(f'captured {captured}: source {source} matches origins {sorted(keys)}')
        found |= keys
    return found, errors


def check(root=ROOT, inventory=None, paths=None):
    root = Path(root)
    if inventory is None:
        inventory = json.loads((root / INVENTORY).read_text())
    errors = validate(inventory)
    if errors:
        return errors, {}
    paths = tracked_files(root) if paths is None else sorted(paths)
    assigned, problems = classify(inventory, paths)
    errors += problems
    rules = inventory['rules']
    third_party = {key for key in inventory['origins'] if key not in OWNED}
    for path, index in assigned.items():
        rule = rules[index]
        if set(rule['origins']) & third_party or 'reviewed' in rule:
            continue
        reasons = ['file type'] if risky_name(path) else content_markers((root / path).read_bytes())
        if reasons:
            errors.append(f'{path}: {", ".join(reasons)} needs a third-party origin or an exact reviewed rule')
    captured = {}
    for path, index in assigned.items():
        if not (path.startswith('evidence/') and path.endswith('/candidate.json')):
            continue
        manifest = json.loads((root / path).read_text())
        if not isinstance(manifest.get('sources'), dict):
            continue
        captured[path], problems = capture_origins(inventory, manifest)
        errors += [f'{path}: {problem}' for problem in problems]
        missing = sorted((captured[path] - set(rules[index]['origins'])) & third_party)
        if missing:
            errors.append(f'{path}: captured inputs come from {missing}, not declared by rule {index}')
    for index, rule in enumerate(rules):
        if rule['kind'] != 'evidence-archive':
            continue
        for archive in rule['paths']:
            group = 'evidence/' + PurePosixPath(archive).name.removesuffix('.tar.gz') + '/'
            held = set()
            for path, other in assigned.items():
                if path.startswith(group):
                    held |= set(rules[other]['origins']) | captured.get(path, set())
            missing = sorted((held - set(rule['origins'])) & third_party)
            if missing:
                errors.append(f'{archive}: holds {group} material from {missing} not declared by rule {index}')
    files = [{'path': path, 'kind': rules[index]['kind'], 'origins': rules[index]['origins'],
              'permission_status': status(inventory, with_embedded(inventory, rules[index]['origins'])),
              'rule': index}
             for path, index in sorted(assigned.items())]
    summary = {'files': len(paths), 'classified': len(files),
               'by_status': dict(sorted(Counter(f['permission_status'] for f in files).items())),
               'by_kind': dict(sorted(Counter(f['kind'] for f in files).items())),
               'by_origin': dict(sorted(Counter(o for f in files for o in f['origins']).items())),
               'inventory': files}
    return errors, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--list', action='store_true', help='print the per-file inventory as JSON')
    args = parser.parse_args(argv)
    errors, summary = check()
    if args.list and not errors:
        print(json.dumps(summary['inventory'], indent=1))
    else:
        print(json.dumps({key: value for key, value in summary.items() if key != 'inventory'} | {'errors': errors}, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
