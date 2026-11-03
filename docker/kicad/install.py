"""Record and probe the KiCad release that the image build installed.

Usage: /usr/bin/python3 install.py DEST

Runs during the image build, after apt installed the newest KiCad 10.0 release and its
libraries from the KiCad PPA and before the apt lists are removed; it needs network
access. It records the release in DEST, which every run with the image uses:

- the kicad-cli version and pcbnew's build version, which must name one release of
  the 10.x line (kicad_install.py, copied beside this file);
- each KiCad package's version, which must be that release;
- the PPA release file apt used (its Date and SHA-256);
- the commit of each library repository's tag for that release (git ls-remote);
- the SHA-256 of kicad-cli and the pcbnew bindings;
- a native probe on an owned board (probe/board.kicad_pcb): pcbnew loads it with the
  footprint and track counts its own text gives, and kicad-cli DRC writes a JSON
  report of that release.

The build fails if any of these does not hold.
"""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location('kicad_install', HERE / 'kicad_install.py')
contract = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(contract)
PPA = 'https://ppa.launchpadcontent.net/kicad/kicad-10.0-releases/ubuntu'
SUITE = 'noble'
LISTS = Path('/var/lib/apt/lists')
BOARD = HERE / 'probe' / 'board.kicad_pcb'
PCBNEW_PROBE = '''
import json, sys
import pcbnew
board = pcbnew.LoadBoard(sys.argv[1])
print(json.dumps({"version": pcbnew.Version(), "build_version": pcbnew.GetBuildVersion(),
                  "footprints": len(board.GetFootprints()), "tracks": len(board.GetTracks())}))
'''


def fail(message):
    sys.exit('kicad install record: ' + message)


def run(argv, env=None):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=600, env=env)
    if result.returncode:
        fail(f"{' '.join(argv[:3])} failed ({result.returncode}): {result.stderr.strip()[-2000:]}")
    return result.stdout


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ppa_release():
    """Date and SHA-256 of the PPA InRelease file that apt-get update fetched."""
    name = re.sub(r'^https?://', '', PPA).replace('/', '_') + f'_dists_{SUITE}_InRelease'
    path = LISTS / name
    if not path.is_file():
        fail(f'apt list {path} is missing; the KiCad PPA was not read')
    dates = re.findall(r'^Date: (.+)$', path.read_text(errors='replace'), re.MULTILINE)
    if len(dates) != 1:
        fail(f'{path} has no single Date field')
    return {'uri': PPA, 'suite': SUITE, 'release_date': dates[0].strip(), 'release_sha256': sha256(path)}


def library_commit(repository, tag):
    refs = {}
    for line in run(['git', 'ls-remote', repository + '.git', f'refs/tags/{tag}', f'refs/tags/{tag}^{{}}']).splitlines():
        commit, _, ref = line.partition('\t')
        refs[ref] = commit
    commit = refs.get(f'refs/tags/{tag}^{{}}') or refs.get(f'refs/tags/{tag}')
    if commit is None:
        fail(f'{repository} has no tag {tag}')
    return {'repository': repository, 'tag': tag, 'commit': commit}


def board_counts(text):
    """Footprints and tracks (segments, arcs, vias) at the board's top level, from its text."""
    return (len(re.findall(r'^\t\(footprint\b', text, re.MULTILINE)),
            len(re.findall(r'^\t\((?:segment|arc|via)\b', text, re.MULTILINE)))


def probe(version, env):
    footprints, tracks = board_counts(BOARD.read_text())
    loaded = json.loads(run(['/usr/bin/python3', '-c', PCBNEW_PROBE, str(BOARD)], env=env).strip().splitlines()[-1])
    if (loaded['footprints'], loaded['tracks']) != (footprints, tracks):
        fail(f'pcbnew loaded {loaded["footprints"]} footprints and {loaded["tracks"]} tracks from the probe '
             f'board; its text has {footprints} and {tracks}')
    if loaded['version'] != version:
        fail(f'pcbnew {loaded["version"]!r} differs from kicad-cli {version!r}')
    with tempfile.TemporaryDirectory(prefix='kicad-probe-') as tmp:
        report_path = Path(tmp) / 'drc.json'
        run(['kicad-cli', 'pcb', 'drc', '--format', 'json', '--units', 'mm', '--severity-all',
             '--output', str(report_path), str(BOARD)], env=env)
        report = json.loads(report_path.read_text())
    if not isinstance(report, dict) or not isinstance(report.get('violations'), list):
        fail('kicad-cli DRC on the probe board wrote no JSON report with violations')
    record = {'board': 'fixtures/dc-path/straight.kicad_pcb', 'board_sha256': sha256(BOARD),
              'footprints': footprints, 'tracks': tracks, 'drc_schema': report.get('$schema'),
              'drc_kicad_version': report.get('kicad_version')}
    return record, loaded['build_version']


def main(dest):
    with tempfile.TemporaryDirectory(prefix='kicad-home-') as home:
        # kicad-cli and pcbnew write their settings under HOME; keep them out of the image.
        env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=home)
        version = run(['kicad-cli', 'version'], env=env).strip()
        if contract.line_release(version) is None:
            fail(f'kicad-cli version {version!r} is not a release of the {contract.RELEASE_MAJOR}.x line')
        packages = {name: run(['dpkg-query', '-W', '-f=${Version}', name]).strip() for name in contract.PACKAGES}
        probe_record, build_version = probe(version, env)
    record = {'schema': contract.INSTALL_RECORD_SCHEMA, 'version': version, 'build_version': build_version,
              'packages': packages, 'source': ppa_release(),
              'libraries': {name: library_commit(repository, version)
                            for name, repository in contract.LIBRARIES.items()},
              'files': {path: sha256(path) for path in contract.NATIVE_FILES},
              'probe': probe_record}
    try:
        contract.check_install_record(record)
    except ValueError as exc:
        fail(f'the install record is not one agent-ee reads: {exc}')
    Path(dest).write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    print(json.dumps(record, sort_keys=True))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        fail('usage: install.py DEST')
    main(sys.argv[1])
