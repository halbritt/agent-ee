"""Install the newest kicad-happy release into the agent-ee image, with the AGENTEE-219 correction.

Usage: /usr/bin/python3 install.py DEST

Runs only during the image build, which has network access. It selects the highest
vX.Y.Z tag of the release line in kicad-happy-contract.json; pre-release tags and the
moving v2 / v2.3 aliases are ignored. It clones that tag, applies correction.patch,
removes .git and runs the analyzers agent-ee uses on the owned probe projects.
Every report must pass the checks agent-ee applies when it reads one
(kicad_happy_format.py, copied beside this file). DEST.install.json then records
the tag, commit, version, output format and the installed-file identity that
every agent-ee run records with its analyzer evidence.

The build fails when no release matches, when the correction no longer applies, or
when a report has an output format or a read field shape that agent-ee does not read.
"""
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = 'https://github.com/aklofas/kicad-happy.git'
HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location('kicad_happy_format', HERE / 'kicad_happy_format.py')
contract = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(contract)
# Every analyzer that agent_ee.kicad_happy runs; schematic and pcb come first.
ANALYZERS = {**contract.ANALYZERS, **contract.SEVERITY_ANALYZERS}


def fail(message):
    sys.exit('kicad-happy install: ' + message)


def run(argv, cwd=None):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=600)
    if result.returncode:
        fail(f"{' '.join(argv[:3])} failed ({result.returncode}): {result.stderr.strip()[-2000:]}")
    return result.stdout


def newest_release(major):
    releases = []
    for line in run(['git', 'ls-remote', '--tags', '--refs', SOURCE]).splitlines():
        match = re.fullmatch(r'[0-9a-f]{40}\trefs/tags/v(\d+)\.(\d+)\.(\d+)', line)
        if match and int(match[1]) == major:
            releases.append(tuple(int(part) for part in match.groups()))
    if not releases:
        fail(f'no v{major}.x.y release tag at {SOURCE}')
    return 'v{}.{}.{}'.format(*max(releases))


def identity(root):
    """The identity agent_ee.kicad_happy.installation computes; the image test compares both."""
    files = {}
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if rel.parts[0] not in ('skills', '.claude-plugin') or any(
                part in ('__pycache__', '.pytest_cache') for part in rel.parts):
            continue
        if path.is_symlink():
            fail('the installation contains a symlink: ' + rel.as_posix())
        if path.is_file():
            files[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    canonical = json.dumps(files, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def output_format(root, formats, probe=HERE / 'probe'):
    """Run the analyzers on the owned probe projects; every report must be one agent-ee reads.

    The severity-gate project runs every analyzer. The AGENTEE-219 reproducer runs
    the schematic analyzer, whose corrected PP-001 notes carry assumptions and
    source_path. Reports are checked exactly as agent-ee checks them at run time:
    kicad_happy_format.check_report, and check_summary for the severity gate's
    analyzers. All reports must name one envelope format."""
    projects = (('severity-gate', 'gate.kicad_sch', ANALYZERS),
                ('AGENTEE-219 reproducer', 'reproducer.kicad_sch', {'schematic': ANALYZERS['schematic']}))
    found = set()
    for project, schematic, analyzers in projects:
        with tempfile.TemporaryDirectory(prefix='kicad-happy-probe-') as tmp:
            work = Path(tmp)
            (work / 'config.json').write_text(json.dumps({'version': 1, 'project': {}, 'suppressions': []}))
            for name, script in analyzers.items():
                run([sys.executable, '-B', str(root / script),
                     *contract.analyzer_args(name, str(probe / schematic), str(probe / 'gate.kicad_pcb'),
                                             str(work), 25.0)], cwd=work)
                try:
                    report = json.loads((work / f'{name}.json').read_text())
                    found.add(contract.check_report(name, report, formats))
                    if name in contract.SEVERITY_ANALYZERS:
                        contract.check_summary(name, report)
                except (OSError, ValueError) as exc:
                    fail(f'{name} report on the {project} probe is outside the contract agent-ee reads: {exc}')
    if len(found) != 1:
        fail(f'analyzers report several output formats {sorted(found)}')
    return found.pop()


def main(dest):
    limits = json.loads((HERE / 'kicad-happy-contract.json').read_text())
    dest = Path(dest)
    tag = newest_release(limits['release_major'])
    run(['git', 'clone', '--quiet', '--depth', '1', '--branch', tag, SOURCE, str(dest)])
    commit = run(['git', '-C', str(dest), 'rev-parse', 'HEAD^{commit}']).strip()
    metadata = json.loads((dest / '.claude-plugin/plugin.json').read_text())
    if metadata.get('name') != 'kicad-happy' or metadata.get('version') != tag[1:]:
        fail(f"{tag} plugin metadata names {metadata.get('name')} {metadata.get('version')}")
    release = identity(dest)
    # AGENTEE-219 correction (KICAD-HAPPY-219, docs/third-party-origins.md).
    patch = HERE / 'correction.patch'
    for flags in (['--check'], []):
        result = subprocess.run(['git', 'apply', *flags, str(patch)], cwd=dest,
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            fail(f'the AGENTEE-219 correction no longer applies to {tag}: {result.stderr.strip()}')
    shutil.rmtree(dest / '.git')
    installed = identity(dest)
    found = output_format(dest, limits['output_formats'])
    if identity(dest) != installed:
        fail('the output-format probe changed the installed files')
    record = {'schema': contract.INSTALL_RECORD_SCHEMA, 'source': SOURCE, 'tag': tag,
              'commit': commit, 'version': metadata['version'], 'output_format': found,
              'release_sha256': release,
              'corrections': [{'origin': 'KICAD-HAPPY-219',
                               'sha256': hashlib.sha256(patch.read_bytes()).hexdigest()}],
              'installation_sha256': installed}
    try:
        contract.check_install_record(record, limits['output_formats'])
    except ValueError as exc:
        fail(f'the install record is not one agent-ee reads: {exc}')
    dest.with_name(dest.name + '.install.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    print(json.dumps(record, sort_keys=True))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        fail('usage: install.py DEST')
    main(sys.argv[1])
