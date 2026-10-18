"""Native ERC and DRC rule severities captured from a pinned project file.

``capture`` reads one committed ``.kicad_pro`` blob at a full commit through the
Git object store only (no working tree, no fetch) and records its ERC and DRC
``rule_severities`` maps with the pinned-external identity of the source. The
source's ERC and DRC exclusions must be empty: an exclusion silences one
violation instance, which is a waiver, not a severity. ``template`` applies a
record's maps to a native project template that carries no severities of its
own, giving a scaffold template whose severity authority is that one record.

A severity map does not waive a violation. Every item kicad-cli still reports,
error or warning, is graded by the ``erc`` and ``drc`` checks as before.
"""
import argparse
import copy
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath

from .block_inventory import _blob
from .catalog import strict_json
from .reference_blocks import _git, _source, verify_external_source
from .storage import json_bytes

SCHEMA = 'agent-ee.rule-severities/1'
SEVERITIES = ('error', 'warning', 'ignore')
# Where each map and its exclusion list live in a KiCad 10 project file.
SECTIONS = {'erc': (('erc', 'rule_severities'), ('erc', 'erc_exclusions')),
            'drc': (('board', 'design_settings', 'rule_severities'),
                    ('board', 'design_settings', 'drc_exclusions'))}


def _at(document, keys):
    for key in keys:
        if not isinstance(document, dict):
            return None
        document = document.get(key)
    return document


def _severities(value, label):
    if not isinstance(value, dict) or not value:
        raise ValueError(f'{label} rule severities must be a nonempty object')
    for key, severity in value.items():
        if not isinstance(key, str) or not key or severity not in SEVERITIES:
            raise ValueError(f'{label} rule severity {key!r} must be one of {", ".join(SEVERITIES)}')
    return dict(sorted(value.items()))


def validate(record):
    """Exact record shape; every severity is a native error, warning or ignore."""
    if not isinstance(record, dict) or record.get('schema') != SCHEMA:
        raise ValueError('rule severities must be ' + SCHEMA)
    if set(record) != {'schema', 'source', 'erc', 'drc'}:
        raise ValueError('rule severities need exactly schema, source, erc and drc')
    if not isinstance(record['source'], dict) or record['source'].get('kind') != 'pinned-external':
        raise ValueError('rule severities source must be a pinned-external citation')
    _source(record['source'], ())
    for section, (keys, _) in SECTIONS.items():
        value = record[section]
        if not isinstance(value, dict) or set(value) != {'locator', 'severities'}:
            raise ValueError(f'rule severities {section} needs exactly locator and severities')
        if value['locator'] != '/' + '/'.join(keys):
            raise ValueError(f'rule severities {section} locator must be /' + '/'.join(keys))
        if list(_severities(value['severities'], section)) != list(value['severities']):
            raise ValueError(f'rule severities {section} keys must be sorted')
    return record


def capture(repository, commit, path, *, repository_label):
    """Record the ERC and DRC severity maps of one committed project file."""
    if _git(repository, 'rev-parse', '--verify', commit + '^{commit}').decode().strip() != commit:
        raise ValueError('rule severities source commit must be a full exact commit')
    if PurePosixPath(path).suffix != '.kicad_pro':
        raise ValueError('rule severities are captured from a .kicad_pro')
    data = _blob(repository, commit, path)
    if data is None:
        raise ValueError('project file is not one regular committed file: ' + path)
    project = strict_json(data)
    record = dict(schema=SCHEMA, source=dict(
        kind='pinned-external', repository=repository_label, commit=commit, path=path,
        blob=hashlib.sha1(b'blob %d\x00' % len(data) + data).hexdigest(),
        sha256=hashlib.sha256(data).hexdigest()))
    for section, (keys, exclusions) in SECTIONS.items():
        if _at(project, exclusions):
            # Violation-bound exclusions are waivers; admitting them is an owner decision.
            raise ValueError(f'source {section} exclusions are waivers, not severities: '
                             + '/'.join(exclusions))
        record[section] = dict(locator='/' + '/'.join(keys),
                               severities=_severities(_at(project, keys), section))
    return validate(record)


def verify(record, repository):
    """Recapture from a local object store; status is verified only on identical maps."""
    validate(record)
    source = record['source']
    status = verify_external_source(source, str(repository))
    if status['status'] != 'verified':
        return status
    if capture(repository, source['commit'], source['path'],
               repository_label=source['repository']) != record:
        raise ValueError('rule severities differ from the pinned project file')
    return status


def template(base, record):
    """The base project with the record's maps as its only severity authority."""
    validate(record)
    project = copy.deepcopy(base)
    if not isinstance(project, dict):
        raise ValueError('template base must be a project object')
    for section, (keys, exclusions) in SECTIONS.items():
        if _at(project, keys) is not None or _at(project, exclusions):
            raise ValueError(f'template base already carries {section} severities or exclusions')
        target = project
        for key in keys[:-1]:
            target = target.setdefault(key, {})
            if not isinstance(target, dict):
                raise ValueError('template base ' + '/'.join(keys[:-1]) + ' must be an object')
        target[keys[-1]] = dict(record[section]['severities'])
    return project


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--repo', required=True, help='local repository; only its object store is read')
    parser.add_argument('--commit', required=True)
    parser.add_argument('--path', required=True, help='committed .kicad_pro path')
    parser.add_argument('--repository-label', required=True)
    parser.add_argument('--base', required=True, help='native project template without severities')
    parser.add_argument('--out', required=True, help='new directory for severities.json and the template')
    parser.add_argument('--name', required=True, help='template basename, e.g. pod')
    args = parser.parse_args(argv)
    try:
        record = capture(args.repo, args.commit, args.path, repository_label=args.repository_label)
        project = template(strict_json(Path(args.base).read_bytes()), record)
        project.setdefault('meta', {})['filename'] = args.name + '.kicad_pro'
        out = Path(args.out)
        out.mkdir(parents=True)
        (out / 'severities.json').write_bytes(json_bytes(record))
        (out / (args.name + '.kicad_pro')).write_bytes(json_bytes(project))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(json.dumps(dict(schema=SCHEMA, status='refused', error=str(exc))))
        return 2
    print(json.dumps(dict(schema=SCHEMA, status='captured', source=record['source']), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
