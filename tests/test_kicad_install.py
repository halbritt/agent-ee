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


class InstallerStageTests(unittest.TestCase):
    """docker/kicad/install.py loads from the stage layout the Dockerfile's COPY lines give."""

    def test_installer_reads_the_shared_contract_and_counts_the_probe_board(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        self.assertIn('COPY docker/kicad/install.py src/agent_ee/kicad_install.py /usr/local/share/agent-ee/kicad/',
                      dockerfile)
        self.assertIn('COPY fixtures/dc-path/straight.kicad_pcb /usr/local/share/agent-ee/kicad/probe/board.kicad_pcb',
                      dockerfile)
        self.assertIn('/usr/bin/python3 /usr/local/share/agent-ee/kicad/install.py /opt/kicad.install.json',
                      dockerfile)
        self.assertNotIn('KICAD_VERSION', dockerfile)
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / 'probe').mkdir()
            shutil.copyfile(ROOT / 'docker/kicad/install.py', stage / 'install.py')
            shutil.copyfile(ROOT / 'src/agent_ee/kicad_install.py', stage / 'kicad_install.py')
            shutil.copyfile(ROOT / 'fixtures/dc-path/straight.kicad_pcb', stage / 'probe/board.kicad_pcb')
            spec = importlib.util.spec_from_file_location('kicad_installer', stage / 'install.py')
            installer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(installer)
            self.assertEqual(installer.contract.INSTALL_RECORD_SCHEMA, kicad_install.INSTALL_RECORD_SCHEMA)
            self.assertEqual(installer.board_counts(installer.BOARD.read_text()), (2, 1))


class PackageMapSelectionTests(unittest.TestCase):
    """The package map of the installed footprint release, else the newest table; the run records which."""

    def test_selection_follows_the_recorded_footprint_release(self):
        tables = sorted(p.name for p in generate.PACKAGE_MAPS.glob('kicad-*-lcsc-1.json'))
        self.assertIn('kicad-10.0.7-lcsc-1.json', tables)
        newest = max(tables, key=lambda name: kicad_install.release(name[len('kicad-'):-len('-lcsc-1.json')]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'kicad.install.json'
            with patch.dict(os.environ, {kicad_install.RECORD_ENV: str(path)}):
                self.assertEqual(generate.package_map_path().name, newest)
                for version, expected in (('10.0.7', 'kicad-10.0.7-lcsc-1.json'), ('10.0.99', newest)):
                    with self.subTest(version=version):
                        path.write_text(json.dumps(record(version)))
                        selected = generate.package_map_path()
                        self.assertEqual(selected.name, expected)
                        table = json.loads(selected.read_text())
                        self.assertEqual(table['version'], selected.name[:-len('.json')])
                        self.assertEqual(table['derivation']['footprint_library'],
                                         'kicad-footprints ' + selected.name[len('kicad-'):-len('-lcsc-1.json')])


if __name__ == '__main__':
    unittest.main()
