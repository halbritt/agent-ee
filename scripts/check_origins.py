#!/usr/bin/env python3
"""Check that docs/file-origins.json classifies every repository file.

The inventory records each file's origin, upstream license and publication
status. This check is structural: it proves every file matches exactly one most
specific rule, that no rule is stale, that files which look like third-party
material are not silently classified as agent-ee material, and that retained
captures and archives declare every origin their inputs came from. It does not
establish anyone's rights or read upstream licenses.

Evidence relocated out of the tree (evidence/relocation.json, AGENTEE-20) is
still classified: its exact paths come from the hash-pinned files manifest, and
the byte-derived facts the check needs (content markers, captured origins) were
recorded from fetched, hash-verified bytes under a recorded basis digest. They
are trusted offline attestations, not a fresh inspection of the absent bytes.
The record must cover every relocated file explicitly (an empty marker list is
the recorded negative); a missing table or entry, a malformed value, a changed
marker pattern, capture source or files manifest refuses until the facts are
re-recorded from a fresh fetch.

    python3 scripts/check_origins.py          # check; exit 1 on any error
    python3 scripts/check_origins.py --list   # per-file inventory as JSON
    python3 scripts/check_origins.py --relocated-facts FETCHED_DIR   # print facts to record
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = 'docs/file-origins.json'
RELOCATION = 'evidence/relocation.json'
RELOCATION_SCHEMA = 'agent-ee.evidence-relocation/1'
FACTS_SCHEMA = 'agent-ee.relocated-origin-facts/2'
SCHEMA = 'agent-ee.file-origins/1'
OWNED = {'AE', 'AE-RUN'}
PROPOSED = 'proposed-mit-pending-owner-approval'
UNRESOLVED = 'unresolved-owner-decision-required'
CONFIRMED = 'owner-confirmed'
DERIVED = 'derived'
STATUSES = (PROPOSED, UNRESOLVED, CONFIRMED)
# A structured owner_record names when, what (verbatim relay) and where the decision was given.
RECORD_FIELDS = ('date', 'decision', 'where')

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
        record = origin.get('owner_record')
        if status == CONFIRMED and isinstance(record, dict):
            errors.extend(f'origin {key}: owner_record needs {field}' for field in RECORD_FIELDS
                          if not isinstance(record.get(field), str) or not record[field].strip())
        elif status == CONFIRMED and not str(record or '').strip():
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


def with_embedded(inventory, ids):
    """Transitive embedded upstream origins (id and upstream_license) outside declared ids.

    When an owner-confirmed origin embeds upstream content whose own origin is
    unresolved, the file status folds to derived and names each embedded
    upstream origin id and upstream_license.
    """
    declared = set(ids)
    found = set()
    pending = [other for key in ids for other in inventory['origins'][key].get('embedded_origins', [])]
    while pending:
        key = pending.pop()
        if key not in found and key not in declared:
            found.add(key)
            pending.extend(inventory['origins'][key].get('embedded_origins', []))
    return [{'id': key, 'upstream_license': inventory['origins'][key]['upstream_license']}
            for key in sorted(found)]


def status(inventory, ids):
    """Unresolved if any declared origin is; derived if an embedded upstream is unresolved; else confirmed or proposed."""
    statuses = {inventory['origins'][key]['permission_status'] for key in ids}
    if UNRESOLVED in statuses:
        return UNRESOLVED
    embedded = {inventory['origins'][item['id']]['permission_status']
                for item in with_embedded(inventory, ids)}
    if UNRESOLVED in embedded:
        return DERIVED
    return CONFIRMED if CONFIRMED in (statuses | embedded) else PROPOSED


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


def facts_basis(inventory):
    """Digest of every input, besides hash-pinned bytes, that relocated origin facts depend on."""
    basis = {'content_markers': {label: pattern.pattern.decode('latin-1')
                                 for label, pattern in sorted(CONTENT_MARKERS.items())},
             'capture_sources': {key: origin.get('capture_sources', [])
                                 for key, origin in sorted(inventory['origins'].items())}}
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()


def relocated_manifest(root, relocation):
    """The hash-pinned files manifest a relocation record names, or ValueError."""
    if relocation.get('schema') != RELOCATION_SCHEMA:
        raise ValueError(f'schema must be {RELOCATION_SCHEMA}')
    raw = (Path(root) / 'evidence' / relocation['files_manifest']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != relocation['files_manifest_sha256']:
        raise ValueError('files manifest digest does not match')
    manifest = json.loads(raw)
    unknown = sorted(set(relocation['relocated_groups']) - set(manifest['groups']))
    if unknown:
        raise ValueError(f'relocated groups missing from manifest: {unknown}')
    return manifest


def relocated_paths(root, relocation):
    manifest = relocated_manifest(root, relocation)
    return {f'evidence/{group}/{name}' for group in relocation['relocated_groups']
            for name in manifest['groups'][group]['files']}


def record_relocated_facts(inventory, relocation, fetched, root=ROOT):
    """Byte-derived facts for every file of the relocated groups, read from a hash-verified fetch.

    Every file gets an explicit content-marker entry (an empty list is the
    recorded negative) and every candidate.json an explicit captured-origins
    entry (null when it has no sources table), so a missing entry is never read
    as clean bytes.
    """
    manifest = relocated_manifest(root, relocation)
    markers, captured = {}, {}
    for group in sorted(relocation['relocated_groups']):
        for name, record in sorted(manifest['groups'][group]['files'].items()):
            source = Path(fetched) / group / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f'fetched {group}/{name} is missing or not a regular file')
            data = source.read_bytes()
            if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError(f'fetched {group}/{name} does not match the files manifest')
            path = f'evidence/{group}/{name}'
            markers[path] = content_markers(data)
            if path.endswith('/candidate.json'):
                origins, problems = candidate_origins(inventory, data)
                if problems:
                    raise ValueError(f'{path}: {problems}')
                captured[path] = origins
    return {'schema': FACTS_SCHEMA, 'basis_sha256': facts_basis(inventory),
            'files_manifest_sha256': relocation['files_manifest_sha256'],
            'content_markers': markers, 'captured_origins': captured}


def candidate_origins(inventory, data):
    """(sorted captured origins or None without a sources table, problems) for candidate.json bytes."""
    sources = json.loads(data).get('sources')
    if not isinstance(sources, dict):
        return None, []
    origins, problems = capture_origins(inventory, {'sources': sources})
    return sorted(origins), problems


def facts_problems(facts, inventory, relocation, expected):
    """Why recorded relocated facts cannot be used; [] only for a complete, well-formed record.

    The facts are trusted offline attestations recorded from verified bytes,
    not a fresh inspection; this establishes their shape, basis and coverage.
    """
    if not isinstance(facts, dict) or facts.get('schema') != FACTS_SCHEMA:
        return [f'origin_facts schema must be {FACTS_SCHEMA}; re-record them with '
                'scripts/check_origins.py --relocated-facts']
    keys = {'schema', 'basis_sha256', 'files_manifest_sha256', 'content_markers', 'captured_origins'}
    if set(facts) != keys:
        return [f'origin_facts must have exactly the keys {sorted(keys)}']
    problems = []
    if facts['basis_sha256'] != facts_basis(inventory):
        problems.append('relocated origin facts were recorded under different content markers '
                        'or capture sources; fetch the relocated groups and re-record them with '
                        'scripts/check_origins.py --relocated-facts')
    if facts['files_manifest_sha256'] != relocation['files_manifest_sha256']:
        problems.append('relocated origin facts were recorded for a different files manifest')
    candidates = {path for path in expected if path.endswith('/candidate.json')}
    for table, paths, allowed, nullable in (('content_markers', expected, set(CONTENT_MARKERS), False),
                                            ('captured_origins', candidates, set(inventory['origins']), True)):
        recorded = facts[table]
        if not isinstance(recorded, dict):
            problems.append(f'origin_facts {table} must be an object')
            continue
        if missing := sorted(paths - set(recorded)):
            problems.append(f'origin_facts {table} lacks {len(missing)} relocated file(s), first {missing[0]}')
        if extra := sorted(set(recorded) - paths):
            problems.append(f'origin_facts {table} names {len(extra)} file(s) outside the relocated groups, '
                            f'first {extra[0]}')
        for path, value in sorted(recorded.items()):
            if value is None and nullable:
                continue
            if (not isinstance(value, list) or not all(isinstance(v, str) for v in value) or
                    value != sorted(set(value)) or not set(value) <= allowed):
                problems.append(f'origin_facts {table} entry for {path} is malformed: {value!r}')
    return problems


def check(root=ROOT, inventory=None, paths=None, relocation=None):
    root = Path(root)
    if inventory is None:
        inventory = json.loads((root / INVENTORY).read_text())
    errors = validate(inventory)
    if errors:
        return errors, {}
    if paths is None:
        paths = tracked_files(root)
        if relocation is None and (root / RELOCATION).is_file():
            relocation = json.loads((root / RELOCATION).read_text())
    paths, virtual, facts = sorted(paths), set(), {}
    if relocation is not None:
        try:
            relocated = relocated_paths(root, relocation)
            virtual = relocated - set(paths)
        except (ValueError, KeyError, OSError) as exc:
            return [f'{RELOCATION}: {exc}'], {}
        facts = relocation.get('origin_facts')
        problems = facts_problems(facts, inventory, relocation, relocated)
        if problems:
            return [f'{RELOCATION}: {problem}' for problem in problems], {}
        # Retained files of relocated groups are read here; their recorded facts must agree.
        for path in sorted(relocated & set(paths)):
            data = (root / path).read_bytes()
            fresh = {'content_markers': content_markers(data)}
            if path in facts['captured_origins']:
                fresh['captured_origins'] = candidate_origins(inventory, data)[0]
            for table, value in fresh.items():
                if facts[table][path] != value:
                    errors.append(f'{RELOCATION}: origin_facts {table} for retained {path} '
                                  f'disagree with its bytes: {facts[table][path]!r} != {value!r}')
        paths = sorted(set(paths) | virtual)
    assigned, problems = classify(inventory, paths)
    errors += problems
    rules = inventory['rules']
    third_party = {key for key in inventory['origins'] if key not in OWNED}
    for path, index in assigned.items():
        rule = rules[index]
        if set(rule['origins']) & third_party or 'reviewed' in rule:
            continue
        reasons = (['file type'] if risky_name(path) else
                   facts['content_markers'][path] if path in virtual else
                   content_markers((root / path).read_bytes()))
        if reasons:
            errors.append(f'{path}: {", ".join(reasons)} needs a third-party origin or an exact reviewed rule')
    captured = {}
    for path, index in assigned.items():
        if not (path.startswith('evidence/') and path.endswith('/candidate.json')):
            continue
        if path in virtual:
            if facts['captured_origins'][path] is None:
                continue
            captured[path] = set(facts['captured_origins'][path])
        else:
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
              'permission_status': status(inventory, rules[index]['origins']),
              'upstream_origins': with_embedded(inventory, rules[index]['origins']),
              'rule': index}
             for path, index in sorted(assigned.items())]
    summary = {'files': len(paths), 'classified': len(files), 'relocated': len(virtual),
               'by_status': dict(sorted(Counter(f['permission_status'] for f in files).items())),
               'by_kind': dict(sorted(Counter(f['kind'] for f in files).items())),
               'by_origin': dict(sorted(Counter(o for f in files for o in f['origins']).items())),
               'inventory': files}
    return errors, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--list', action='store_true', help='print the per-file inventory as JSON')
    parser.add_argument('--relocated-facts', type=Path, metavar='FETCHED_DIR',
                        help='print origin facts for the relocated groups fetched (hash-verified) into FETCHED_DIR')
    args = parser.parse_args(argv)
    if args.relocated_facts:
        inventory = json.loads((ROOT / INVENTORY).read_text())
        relocation = json.loads((ROOT / RELOCATION).read_text())
        print(json.dumps(record_relocated_facts(inventory, relocation, args.relocated_facts),
                         indent=1, sort_keys=True))
        return 0
    errors, summary = check()
    if args.list and not errors:
        print(json.dumps(summary['inventory'], indent=1))
    else:
        print(json.dumps({key: value for key, value in summary.items() if key != 'inventory'} | {'errors': errors}, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
