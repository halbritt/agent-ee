"""The KiCad release a run uses is the one installed and recorded, never one fixed version.

Pure tests: no KiCad runs. The image build runs docker/kicad/install.py, which writes the
install record checked here; native modules compare reports with the recorded version.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import generate, kicad_install
from agent_ee.kicad_install import check_install_record, line_release, read_install_record, upstream_version

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '8975715167d1a76f9b44be155ac02326f704cdbb'


def record(version='10.0.8'):
    """An install record in the shape docker/kicad/install.py writes."""
    return {'schema': kicad_install.INSTALL_RECORD_SCHEMA, 'version': version,
            'build_version': f'{version}-{version}~ubuntu24.04.1',
            'packages': {name: f'{version}~ubuntu24.04.1' for name in kicad_install.PACKAGES},
            'source': {'uri': 'https://ppa.launchpadcontent.net/kicad/kicad-10.0-releases/ubuntu',
                       'suite': 'noble', 'release_date': 'Fri, 30 Oct 2026  1:17:10 UTC',
                       'release_sha256': 'f' * 64},
            'libraries': {name: {'repository': url, 'tag': version, 'commit': COMMIT}
                          for name, url in kicad_install.LIBRARIES.items()},
            'files': {path: 'a' * 64 for path in kicad_install.NATIVE_FILES},
            'probe': {'board': 'fixtures/dc-path/straight.kicad_pcb', 'board_sha256': 'b' * 64,
                      'footprints': 2, 'tracks': 1, 'drc_schema': 'https://schemas.kicad.org/drc.v1.json',
                      'drc_kicad_version': version}}


class ReleaseTests(unittest.TestCase):
    def test_any_release_of_the_10x_line_and_nothing_else(self):
        for version in ('10.0.7', '10.0.8', '10.1.0', '10.12.3'):
            with self.subTest(version=version):
                self.assertEqual(line_release(version), version)
        for version in ('9.0.7', '11.0.0', '10.0.8-rc1', '10.0.7-45-g1234abc', '10.0', '', None, 10):
            with self.subTest(version=version):
                self.assertIsNone(line_release(version))

    def test_package_versions_give_their_upstream_release(self):
        # Ubuntu's own 7.0 packages parse too, so a record mixing them with the PPA's release is refused.
        for package, upstream in (('10.0.8~ubuntu24.04.1', '10.0.8'), ('1:10.0.8-1', '10.0.8'),
                                  ('7.0.11+dfsg-1build4', '7.0.11')):
            self.assertEqual(upstream_version(package), upstream)
        for package in ('10.0', 'master', '', None):
            with self.subTest(package=package):
                self.assertIsNone(upstream_version(package))


class InstallRecordTests(unittest.TestCase):
    def test_coherent_records_of_any_line_release_are_read(self):
        for version in ('10.0.7', '10.0.8', '10.1.0'):
            with self.subTest(version=version):
                self.assertEqual(check_install_record(record(version)), record(version))

    def test_incoherent_or_malformed_records_are_refused(self):
        mutations = {
            'extra field': lambda r: r.update(note='x'),
            'other schema': lambda r: r.update(schema='agent-ee.kicad-install/2'),
            'other line': lambda r: r.update(version='9.0.7', build_version='9.0.7'),
            'prerelease': lambda r: r.update(version='10.0.9-rc1'),
            'build of another release': lambda r: r.update(build_version='10.0.7-10.0.7~ubuntu24.04.1'),
            'package of another release': lambda r: r['packages'].update({'kicad-footprints': '10.0.7~ubuntu24.04.1'}),
            'package missing': lambda r: r['packages'].pop('kicad-packages3d'),
            'library tag of another release': lambda r: r['libraries']['kicad-symbols'].update(tag='10.0.7'),
            'library repository': lambda r: r['libraries']['kicad-symbols'].update(repository='https://example.invalid'),
            'library commit': lambda r: r['libraries']['kicad-packages3d'].update(commit='master'),
            'file digest': lambda r: r['files'].update({'/usr/bin/kicad-cli': 'not-a-digest'}),
            'file missing': lambda r: r['files'].pop('/usr/lib/python3/dist-packages/_pcbnew.so'),
            'source digest': lambda r: r['source'].update(release_sha256='x'),
            'probe DRC of another release': lambda r: r['probe'].update(drc_kicad_version='10.0.7'),
            'probe without tracks': lambda r: r['probe'].update(tracks=0),
        }
        for name, mutate in mutations.items():
            changed = record()
            mutate(changed)
            with self.subTest(mutation=name), self.assertRaises(ValueError):
                check_install_record(changed)

    def test_read_record_measures_path_and_digest_or_reports_none(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'kicad.install.json'
            self.assertIsNone(read_install_record(path))
            raw = (json.dumps(record(), indent=2, sort_keys=True) + '\n').encode()
            path.write_bytes(raw)
            with patch.dict(os.environ, {kicad_install.RECORD_ENV: str(path)}):
                found = read_install_record()
            self.assertEqual(found['path'], str(path))
            self.assertEqual(found['record'], record())
            self.assertEqual(found['sha256'], hashlib.sha256(raw).hexdigest())
            for raw in (b'{', json.dumps({**record(), 'version': '9.0.7'}).encode()):
                path.write_bytes(raw)
                with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                    read_install_record(path)


def dockerfile_instructions():
    """The Dockerfile's instructions with continuation lines joined."""
    joined = (ROOT / 'Dockerfile').read_text().replace('\\\n', ' ')
    return [line.strip() for line in joined.splitlines() if line.strip() and not line.lstrip().startswith('#')]


class InstallerStageTests(unittest.TestCase):
    """docker/kicad/install.py runs from the stage layout the Dockerfile's COPY lines give."""

    def test_installer_reads_the_shared_contract_and_counts_the_probe_board(self):
        instructions = dockerfile_instructions()
        copies = {}
        for line in instructions:
            words = line.split()
            if words[0] == 'COPY' and not any(word.startswith('--from=') for word in words):
                *sources, destination = [word for word in words[1:] if not word.startswith('--')]
                for source in sources:
                    copies[source] = destination
        stage = copies['docker/kicad/install.py']
        # The shared contract sits beside the installer and the owned board under probe/.
        self.assertEqual(copies['src/agent_ee/kicad_install.py'], stage)
        self.assertEqual(copies['fixtures/dc-path/straight.kicad_pcb'], stage + 'probe/board.kicad_pcb')
        self.assertTrue(any(line.startswith('RUN ') and stage + 'install.py' in line and '/opt/kicad.install.json' in line
                            for line in instructions))
        # No KiCad version is fixed in the Dockerfile.
        self.assertFalse(any(line.startswith('ARG KICAD_VERSION') for line in instructions))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for source in ('docker/kicad/install.py', 'src/agent_ee/kicad_install.py', 'fixtures/dc-path/straight.kicad_pcb'):
                target = root / copies[source].lstrip('/')
                if copies[source].endswith('/'):
                    target = target / Path(source).name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / source, target)
            spec = importlib.util.spec_from_file_location('kicad_installer', root / stage.lstrip('/') / 'install.py')
            installer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(installer)
            self.assertEqual(installer.contract.INSTALL_RECORD_SCHEMA, kicad_install.INSTALL_RECORD_SCHEMA)
            self.assertEqual(installer.board_counts(installer.BOARD.read_text()), (2, 1))


class PackageMapSelectionTests(unittest.TestCase):
    """The package map of the installed footprint release, or a refusal; never another release's table."""

    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.record_path, self.status = directory / 'kicad.install.json', directory / 'status'
        self.enterContext(patch.dict(os.environ, {kicad_install.RECORD_ENV: str(self.record_path)}))
        self.enterContext(patch.object(generate, 'DPKG_STATUS', self.status))

    def dpkg(self, version, status='install ok installed'):
        self.status.write_text(f'Package: kicad\nStatus: {status}\nVersion: {version}\n\n'
                               f'Package: kicad-footprints\nStatus: {status}\nVersion: {version}\n'
                               'Description: KiCad footprints\n multi-line description\n')

    def test_the_recorded_or_installed_footprint_release_selects_its_table(self):
        self.record_path.write_text(json.dumps(record('10.0.7')))
        self.dpkg('10.0.8~ubuntu26.04.1')
        self.assertEqual(generate.footprint_release(), ('10.0.7', str(self.record_path)))
        selected = generate.package_map_path()
        self.assertEqual(selected.name, 'kicad-10.0.7-lcsc-1.json')
        table = json.loads(selected.read_text())
        self.assertEqual((table['version'], table['derivation']['footprint_library']),
                         ('kicad-10.0.7-lcsc-1', 'kicad-footprints 10.0.7'))
        # A host without an install record reads its installed package.
        self.record_path.unlink()
        self.assertEqual(generate.footprint_release(), ('10.0.8', str(self.status)))
        self.assertEqual(generate.package_map_path().name, 'kicad-10.0.8-lcsc-1.json')
        # An explicitly requested release is used as named.
        self.assertEqual(generate.package_map_path('10.0.7').name, 'kicad-10.0.7-lcsc-1.json')

    def test_missing_table_corrupt_record_or_unknown_release_refuses(self):
        self.record_path.write_text(json.dumps(record('10.0.99')))
        with self.assertRaisesRegex(ValueError, 'no package map for kicad-footprints 10.0.99'):
            generate.package_map_path()
        self.record_path.write_text('{')
        with self.assertRaisesRegex(ValueError, 'not JSON'):
            generate.package_map_path()
        self.record_path.unlink()
        for status in (None, 'deinstall ok config-files'):
            with self.subTest(dpkg=status):
                if status is None:
                    self.status.write_text('Package: kicad\nStatus: install ok installed\nVersion: 10.0.8\n')
                else:
                    self.dpkg('10.0.8', status)
                with self.assertRaisesRegex(ValueError, 'release is unknown'):
                    generate.package_map_path()
        with self.assertRaisesRegex(ValueError, 'no package map for kicad-footprints 9.0.7'):
            generate.package_map_path('9.0.7')


if __name__ == '__main__':
    unittest.main()
