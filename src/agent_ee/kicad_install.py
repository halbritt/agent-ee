"""The KiCad release a run uses: the image's install record and the release-line check.

Standard library only: the image build loads this file beside docker/kicad/install.py.

The image installs the newest KiCad 10.0 release in the KiCad PPA, with its symbol,
footprint, 3D-model and template libraries, when it is built. docker/kicad/install.py
writes /opt/kicad.install.json (check_install_record below). Every run with that image
uses those exact versions and records the version and binary digests it used with its
evidence; nothing requires one fixed KiCad version. Native readers refuse only a release
outside the 10.x line their formats are written for (line_release). Behaviour that
differs between releases is keyed on the recorded version in kicad_quirks.
"""
import hashlib
import json
import os
import re
from pathlib import Path

INSTALL_RECORD_SCHEMA = 'agent-ee.kicad-install/1'
RECORD_ENV = 'AGENT_EE_KICAD_INSTALL_RECORD'
DEFAULT_RECORD = '/opt/kicad.install.json'
# The release line the native readers and writers are written for. A release outside it
# can change file formats and report shapes; it is refused where a format is read.
RELEASE_MAJOR = 10
PACKAGES = ('kicad', 'kicad-symbols', 'kicad-footprints', 'kicad-packages3d', 'kicad-templates')
# Library packages are built from the library repositories' tag of the same release.
LIBRARIES = {'kicad-symbols': 'https://gitlab.com/kicad/libraries/kicad-symbols',
             'kicad-footprints': 'https://gitlab.com/kicad/libraries/kicad-footprints',
             'kicad-packages3d': 'https://gitlab.com/kicad/libraries/kicad-packages3D',
             'kicad-templates': 'https://gitlab.com/kicad/libraries/kicad-templates'}
NATIVE_FILES = ('/usr/bin/kicad-cli', '/usr/lib/python3/dist-packages/pcbnew.py',
                '/usr/lib/python3/dist-packages/_pcbnew.so')
RECORD_FIELDS = ('schema', 'version', 'build_version', 'packages', 'source', 'libraries', 'files', 'probe')
SOURCE_FIELDS = ('uri', 'suite', 'release_date', 'release_sha256')
PROBE_FIELDS = ('board', 'board_sha256', 'footprints', 'tracks', 'drc_schema', 'drc_kicad_version')
_RELEASE = re.compile(r'(\d+)\.(\d+)\.(\d+)')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_COMMIT = re.compile(r'[0-9a-f]{40}')


def release(version):
    """(major, minor, patch) of a MAJOR.MINOR.PATCH release string, else None."""
    match = _RELEASE.fullmatch(version) if isinstance(version, str) else None
    return tuple(map(int, match.groups())) if match else None


def line_release(version):
    """version when it is a MAJOR.MINOR.PATCH release of the 10.x line, else None.

    Release candidates, nightly and branch builds (10.0.8-rc1, 10.0.7-45-g...) are not
    releases of the line."""
    parsed = release(version)
    return version if parsed and parsed[0] == RELEASE_MAJOR else None


def upstream_version(package_version):
    """The upstream release of a Debian package version: '10.0.8~ubuntu24.04.1' gives '10.0.8'."""
    match = re.fullmatch(r'(?:\d+:)?(\d+\.\d+\.\d+)(?:[~+-][0-9A-Za-z.~+-]*)?', package_version) \
        if isinstance(package_version, str) else None
    return match[1] if match else None


def _exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f'{label} must have exactly the fields {", ".join(fields)}')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')
    return value


def check_install_record(record):
    """Raise ValueError unless record is one docker/kicad/install.py writes, internally coherent.

    The version is a release of the 10.x line; pcbnew's build version, every package's
    upstream release, every library tag and the probe's DRC report name that release."""
    _exact(record, RECORD_FIELDS, 'KiCad install record')
    if record['schema'] != INSTALL_RECORD_SCHEMA:
        raise ValueError(f"KiCad install record schema is {record['schema']!r}")
    version = record['version']
    if line_release(version) is None:
        raise ValueError(f'KiCad install record version {version!r} is not a release of the '
                         f'{RELEASE_MAJOR}.x line')
    build = _text(record['build_version'], 'build_version')
    if build != version and not build.startswith(version + '-'):
        raise ValueError(f'pcbnew build version {build!r} is not release {version}')
    _exact(record['packages'], PACKAGES, 'packages')
    for name, package in record['packages'].items():
        if upstream_version(package) != version:
            raise ValueError(f'package {name} {package!r} is not release {version}')
    _exact(record['source'], SOURCE_FIELDS, 'source')
    for key in SOURCE_FIELDS:
        _text(record['source'][key], 'source ' + key)
    if not _SHA256.fullmatch(record['source']['release_sha256']):
        raise ValueError('source release_sha256 must be a SHA-256 digest')
    _exact(record['libraries'], tuple(LIBRARIES), 'libraries')
    for name, library in record['libraries'].items():
        _exact(library, ('repository', 'tag', 'commit'), 'library ' + name)
        if (library['repository'] != LIBRARIES[name] or library['tag'] != version
                or not isinstance(library['commit'], str) or not _COMMIT.fullmatch(library['commit'])):
            raise ValueError(f'library {name} must name {LIBRARIES[name]} tag {version} and its commit')
    _exact(record['files'], NATIVE_FILES, 'files')
    if any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in record['files'].values()):
        raise ValueError('installed file digests must be SHA-256 digests')
    probe = record['probe']
    _exact(probe, PROBE_FIELDS, 'probe')
    _text(probe['board'], 'probe board')
    if not isinstance(probe['board_sha256'], str) or not _SHA256.fullmatch(probe['board_sha256']):
        raise ValueError('probe board_sha256 must be a SHA-256 digest')
    if any(type(probe[key]) is not int or probe[key] < 1 for key in ('footprints', 'tracks')):
        raise ValueError('probe footprint and track counts must be positive integers')
    if probe['drc_kicad_version'] != version:
        raise ValueError(f"probe DRC report names KiCad {probe['drc_kicad_version']!r}, not {version}")
    _text(probe['drc_schema'], 'probe drc_schema')
    return record


def record_path():
    return Path(os.environ.get(RECORD_ENV) or DEFAULT_RECORD)


def read_install_record(path=None):
    """{'path', 'sha256', 'record'} for the image's install record, or None when there is none.

    A host install without the image has no record; runs still record what they execute.
    A record that exists but is not one the installer writes raises ValueError."""
    path = Path(path) if path is not None else record_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        record = json.loads(raw.decode('utf-8'))
    except ValueError as exc:
        raise ValueError(f'KiCad install record {path} is not JSON') from exc
    check_install_record(record)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'record': record}
