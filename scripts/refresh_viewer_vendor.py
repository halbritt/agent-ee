#!/usr/bin/env python3
"""Refresh the vendored three.js viewer files to the current upstream release.

Reads src/agent_ee/viewer_assets/vendor/manifest.json, resolves the newest rNNN release tag
of its upstream repository (git ls-remote) unless --release names one, and fetches every
file from the manifest's own source URLs at that tag. It applies the recorded transforms and
verifies the result against the files it would replace:

- every relative import of a vendored module names a vendored file;
- every name a vendored module imports from 'three' is exported by three.module.js;
- every THREE.<name>, GLTFLoader and OrbitControls the viewer uses is still exported;
- every loader and controls member the viewer uses, and each controls event it listens to,
  still appears where it appeared in the current files, and the loader and controls
  constructors take the same parameters.

Only when every check passes are the files written and the manifest rewritten with the
release, each file's source URL, upstream SHA-256 and vendored SHA-256. The LICENSE stays
next to the files. --check prints the report and writes nothing. Exit status: 0 written or
already current, 1 a check failed (nothing written), 2 a fetch or usage error.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'src/agent_ee/viewer_assets/vendor'
VIEWER = ROOT / 'src/agent_ee/viewer_assets/viewer.js'
MAX_BYTES = 8 * 1024**2
TAG = re.compile(r'r([0-9]+)')
# Each recorded transform: the exact upstream text it replaces (once) and its replacement.
TRANSFORMS = {
    'Local BufferGeometryUtils import flattened to ./BufferGeometryUtils.js':
        ("from '../utils/BufferGeometryUtils.js'", "from './BufferGeometryUtils.js'"),
}
IMPORT = re.compile(r'''(?m)^import\s*(?:([\w*{},$\s]+?)\s*from\s*)?['"]([^'"]+)['"]''')
EXPORT = re.compile(r'export\s*\{([^}]*)\}')


class CheckFailed(Exception):
    pass


def latest_release(repository):
    listing = subprocess.run(['git', 'ls-remote', '--tags', '--refs', repository], check=True,
                             capture_output=True, text=True, timeout=60).stdout
    numbers = [int(m.group(1)) for line in listing.splitlines()
               if (m := TAG.fullmatch(line.rsplit('refs/tags/', 1)[-1]))]
    if not numbers:
        raise CheckFailed(f'no rNNN release tags at {repository}')
    return f'r{max(numbers)}'


def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise CheckFailed(f'{url} does not exist (HTTP 404): the release does not publish '
                              'this file at the source path the manifest records') from exc
        raise OSError(f'{url}: HTTP {exc.code}') from exc
    if len(payload) > MAX_BYTES:
        raise CheckFailed(f'{url} exceeds {MAX_BYTES} bytes')
    return payload


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def at_release(url, old, new):
    marker = f'/{old}/'
    if url.count(marker) != 1:
        raise CheckFailed(f'source URL does not name release {old} once: {url}')
    return url.replace(marker, f'/{new}/')


def transform(name, payload, transforms):
    text = payload.decode('utf-8')
    for description in transforms:
        if description not in TRANSFORMS:
            raise CheckFailed(f'{name}: unknown recorded transform {description!r}')
        old, new = TRANSFORMS[description]
        if text.count(old) != 1:
            raise CheckFailed(f'{name}: transform {description!r} expects {old!r} once, found {text.count(old)}')
        text = text.replace(old, new)
    return text.encode('utf-8')


def imports(text):
    """[(names, specifier)] for module-level imports (not examples inside comments)."""
    found = []
    for clause, specifier in IMPORT.findall(text):
        names = set()
        if '{' in clause:
            for item in clause[clause.index('{') + 1:clause.rindex('}')].split(','):
                if item.strip():
                    names.add(item.split(' as ')[0].strip())
        found.append((names, specifier))
    return found


def exports(text):
    names = set()
    for clause in EXPORT.findall(text):
        for item in clause.split(','):
            if item.strip():
                names.add(item.split(' as ')[-1].strip())
    return names


def viewer_uses(viewer):
    return {'three': set(re.findall(r'\bTHREE\.([A-Za-z_$][\w$]*)', viewer)),
            'controls': set(re.findall(r'\bcontrols\.([A-Za-z_$][\w$]*)', viewer)),
            'loader': set(re.findall(r'\bloader\.([A-Za-z_$][\w$]*)', viewer)),
            'events': set(re.findall(r"controls\.addEventListener\('([^']+)'", viewer))}


def constructor(text, cls):
    match = re.search(r'class ' + cls + r'\b[^{]*\{.*?\n\tconstructor\(([^)]*)\)', text, re.S)
    return re.sub(r'\s+', ' ', match.group(1)).strip() if match else None


def api(files, viewer):
    """The parts of the vendored API the viewer depends on, from the given file set."""
    text = {name: payload.decode('utf-8', 'replace') for name, payload in files.items()}
    uses = viewer_uses(viewer)
    core = text.get('three.module.js', '') + text.get('three.core.min.js', '')
    member = lambda name, source: bool(re.search(r'(?<![\w$])' + re.escape(name) + r'(?![\w$])', source))
    return {
        'three_exports': {n: n in exports(text.get('three.module.js', '')) for n in sorted(uses['three'])},
        'module_exports': {'GLTFLoader': 'GLTFLoader' in exports(text.get('GLTFLoader.js', '')),
                           'OrbitControls': 'OrbitControls' in exports(text.get('OrbitControls.js', ''))},
        'controls_members': {n: member(n, text.get('OrbitControls.js', '') + core) for n in sorted(uses['controls'])},
        'loader_members': {n: member(n, text.get('GLTFLoader.js', '') + core) for n in sorted(uses['loader'])},
        'controls_events': {e: f"type: '{e}'" in text.get('OrbitControls.js', '') for e in sorted(uses['events'])},
        'constructors': {'GLTFLoader': constructor(text.get('GLTFLoader.js', ''), 'GLTFLoader'),
                         'OrbitControls': constructor(text.get('OrbitControls.js', ''), 'OrbitControls')},
    }


def closure_problems(files):
    """Relative imports outside the vendored set; names imported from 'three' it does not export."""
    problems = []
    three = exports(files['three.module.js'].decode('utf-8'))
    for name, payload in sorted(files.items()):
        if not name.endswith('.js'):
            continue
        for names, specifier in imports(payload.decode('utf-8')):
            if specifier == 'three':
                problems += [f'{name} imports {n} from three, which three.module.js does not export'
                             for n in sorted(names - three)]
            elif specifier.startswith('.') and specifier.removeprefix('./') not in files:
                problems.append(f'{name} imports {specifier}, which is not vendored')
            elif not specifier.startswith('.') and specifier != 'three':
                problems.append(f'{name} imports {specifier}, which is not local')
    return problems


def api_problems(current, refreshed):
    problems = []
    for section, values in refreshed.items():
        for key, value in values.items():
            before = current[section].get(key)
            if value != before:
                problems.append(f'{section} {key}: {before!r} in the current files, {value!r} at the new release')
    return problems


def refresh(vendor=VENDOR, viewer=VIEWER, release=None, check=False, get=fetch, tags=latest_release):
    """Return (report, status). Writes the files and manifest only when status is 'written'."""
    manifest = json.loads((vendor / 'manifest.json').read_text(encoding='utf-8'))
    match = re.fullmatch(r'https://github\.com/([^/]+/[^/]+)/tree/(r[0-9]+)', manifest['upstream'])
    if not match:
        raise CheckFailed(f"manifest upstream is not a GitHub release tree: {manifest['upstream']}")
    repository, current_release = match.groups()
    release = release or tags(f'https://github.com/{repository}')
    if not TAG.fullmatch(release):
        raise CheckFailed(f'not a release tag: {release}')
    report = {'package': manifest['package'], 'current': {'release': current_release, 'version': manifest['version']},
              'release': release, 'version': f'0.{release[1:]}.0', 'files': {}}
    current = {name: (vendor / name).read_bytes() for name in manifest['files']}
    for name, entry in manifest['files'].items():
        if sha256(current[name]) != entry['sha256']:
            raise CheckFailed(f'{name} differs from its manifest entry; restore it before a refresh')
    if release == current_release:
        return report, 'current'
    refreshed, entries = {}, {}
    for name, entry in manifest['files'].items():
        source = at_release(entry['source'], current_release, release)
        upstream = get(source)
        payload = transform(name, upstream, entry['transforms'])
        refreshed[name] = payload
        entries[name] = {'source': source, 'upstream_sha256': sha256(upstream), 'sha256': sha256(payload),
                         'transforms': entry['transforms']}
        report['files'][name] = {'upstream_sha256': entries[name]['upstream_sha256'],
                                 'sha256': entries[name]['sha256'], 'changed': payload != current[name]}
    viewer_text = viewer.read_text(encoding='utf-8')
    report['api'] = api(refreshed, viewer_text)
    problems = closure_problems(refreshed) + api_problems(api(current, viewer_text), report['api'])
    report['problems'] = problems
    if problems:
        return report, 'refused'
    if check:
        return report, 'checked'
    for name, payload in refreshed.items():
        (vendor / name).write_bytes(payload)
    manifest.update(version=report['version'], upstream=f'https://github.com/{repository}/tree/{release}',
                    files=entries)
    (vendor / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return report, 'written'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--release', help='release tag such as r187 (default: the newest upstream rNNN tag)')
    parser.add_argument('--check', action='store_true', help='fetch and verify only; write nothing')
    parser.add_argument('--vendor', type=Path, default=VENDOR)
    args = parser.parse_args(argv)
    try:
        report, status = refresh(args.vendor, release=args.release, check=args.check)
    except CheckFailed as exc:
        print(f'refresh refused: {exc}', file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as exc:
        print(f'refresh failed: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(dict(report, status=status), indent=2))
    return 1 if status == 'refused' else 0


if __name__ == '__main__':
    sys.exit(main())
