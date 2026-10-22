"""Evidence relocated to the owner's store (AGENTEE-20); no network or credentials used.

The tree keeps only what tests, fixture tools and linked summaries read. These
tests pin that the kept bytes are exactly the manifest's, that nothing else of
a relocated group lingers, and that absent bytes produce typed refusals.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'scripts/{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


origins = load('check_origins')
archive = load('evidence_archive')
RELOCATION = json.loads((ROOT / 'evidence/relocation.json').read_text())
MANIFEST = json.loads((ROOT / 'evidence/files-manifest.json').read_text())


def tracked(prefix):
    out = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z', '--', prefix], capture_output=True, check=True)
    return sorted(p for p in out.stdout.decode().split('\0') if p)


class RelocationRecordTests(unittest.TestCase):
    def test_record_pins_manifest_and_names_store_without_secrets(self):
        raw = (ROOT / 'evidence/files-manifest.json').read_bytes()
        self.assertEqual(RELOCATION['schema'], 'agent-ee.evidence-relocation/1')
        self.assertEqual(RELOCATION['files_manifest_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(RELOCATION['store']['base_url'], 's3://agent-ee-evidence')
        self.assertEqual(RELOCATION['store']['credentials_file'], '~/.config/agent-ee/evidence-s3.env')
        text = (ROOT / 'evidence/relocation.json').read_text()
        for secret_shape in ('AWS_SECRET_ACCESS_KEY', 'GK', 'aws_secret'):
            self.assertNotIn(secret_shape, text)
        self.assertLessEqual(set(RELOCATION['relocated_groups']), set(MANIFEST['groups']))
        self.assertLessEqual(set(RELOCATION['retained_in_tree']), set(RELOCATION['relocated_groups']))

    def test_tree_holds_exactly_the_retained_manifest_bytes(self):
        if not (ROOT / '.git').exists():
            self.skipTest('needs the Git index to list tracked evidence')
        for group, files in MANIFEST['groups'].items():
            relocated = group in RELOCATION['relocated_groups']
            expected = RELOCATION['retained_in_tree'].get(group, []) if relocated else sorted(files['files'])
            with self.subTest(group=group):
                self.assertEqual(tracked(f'evidence/{group}/'), sorted(f'evidence/{group}/{p}' for p in expected))
                for name in expected:
                    data = (ROOT / 'evidence' / group / name).read_bytes()
                    record = files['files'][name]
                    self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (record['bytes'], record['sha256']))

    def test_relocated_paths_stay_classified_from_recorded_facts(self):
        errors, summary = origins.check(ROOT)
        self.assertEqual(errors, [])
        relocated = sum(len(MANIFEST['groups'][g]['files']) - len(RELOCATION['retained_in_tree'].get(g, []))
                        for g in RELOCATION['relocated_groups'])
        if (ROOT / '.git').exists():
            self.assertEqual(summary['relocated'], relocated)
        self.assertEqual(RELOCATION['origin_facts']['basis_sha256'],
                         origins.facts_basis(json.loads((ROOT / 'docs/file-origins.json').read_text())))


class ArchiveRefusalTests(unittest.TestCase):
    def test_relocated_archive_is_a_typed_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'archives').mkdir()
            (root / 'archives/manifest.json').write_text(json.dumps({'demo': {
                'archive': 'demo.tar.gz', 'sha256': '0' * 64, 'bytes': 1}}))
            with self.assertRaisesRegex(archive.ArchiveRelocated, 'fetch the .archives. group'):
                archive.restore('demo', root)
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/evidence_archive.py'), 'restore', 'demo',
                                     '--evidence', str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn('refused (archive relocated)', result.stderr)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['archives'])


def rule(paths, origins_, kind='retained-output', **extra):
    return {'paths': paths, 'kind': kind, 'origins': origins_, **extra}


class RelocatedOriginCheckTests(unittest.TestCase):
    """Synthetic trees: virtual paths come from the manifest, facts from the record."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        files = {'run/candidate.json': b'{"sources": {"p/x.kicad_sch": "/x/donor/x.kicad_sch"}}',
                 'run/log.txt': b'(kicad' b'_sch (version 1))', 'run/kept.txt': b'kept'}
        manifest = {'schema': 'agent-ee.evidence-files/1', 'groups': {'grp': {'files': {
            name.split('/', 1)[1]: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            for name, data in files.items()}}}}
        (self.root / 'evidence/grp').mkdir(parents=True)
        raw = json.dumps(manifest).encode()
        (self.root / 'evidence/files-manifest.json').write_bytes(raw)
        (self.root / 'evidence/grp/kept.txt').write_bytes(b'kept')
        self.fetched = self.root / 'fetched'
        for name, data in files.items():
            target = self.fetched / 'grp' / name.split('/', 1)[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        meta = {'name': 'n', 'upstream_license': 'u', 'owner_decision': 'd'}
        self.inventory = {'schema': 'agent-ee.file-origins/1', 'kinds': {'retained-output': 'x', 'owned-metadata': 'x'},
                          'origins': {'AE-RUN': {**meta, 'permission_status': 'proposed-mit-pending-owner-approval'},
                                      'T3': {**meta, 'permission_status': 'unresolved-owner-decision-required',
                                             'capture_sources': ['/donor/']}},
                          'rules': [rule(['evidence/grp/'], ['AE-RUN', 'T3']),
                                    rule(['evidence/files-manifest.json'], ['AE-RUN'], 'owned-metadata')]}
        self.relocation = {'schema': origins.RELOCATION_SCHEMA, 'files_manifest': 'files-manifest.json',
                           'files_manifest_sha256': hashlib.sha256(raw).hexdigest(), 'relocated_groups': ['grp']}
        self.relocation['origin_facts'] = origins.record_relocated_facts(
            self.inventory, self.relocation, self.fetched, self.root)
        self.paths = ['evidence/files-manifest.json', 'evidence/grp/kept.txt']

    def tearDown(self):
        self.directory.cleanup()

    def check(self):
        return origins.check(self.root, self.inventory, self.paths, self.relocation)

    def test_recorded_facts_classify_absent_files(self):
        facts = self.relocation['origin_facts']
        self.assertEqual(facts['content_markers'], {'evidence/grp/log.txt': ['KiCad design text']})
        self.assertEqual(facts['captured_origins'], {'evidence/grp/candidate.json': ['T3']})
        errors, summary = self.check()
        self.assertEqual(errors, [])
        self.assertEqual((summary['files'], summary['relocated']), (4, 2))

    def test_recorded_content_markers_and_captures_still_enforce_rules(self):
        self.inventory['rules'][0]['origins'] = ['AE-RUN']
        errors, _ = self.check()
        self.assertIn('evidence/grp/log.txt: KiCad design text needs a third-party origin or an exact reviewed rule',
                      errors)
        self.assertIn("evidence/grp/candidate.json: captured inputs come from ['T3'], not declared by rule 0", errors)

    def test_changed_basis_or_missing_facts_refuse(self):
        self.inventory['origins']['T3']['capture_sources'] = ['/donor-moved/']
        self.assertIn('relocated origin facts were recorded under different', self.check()[0][0])
        self.inventory['origins']['T3']['capture_sources'] = ['/donor/']
        del self.relocation['origin_facts']['captured_origins']['evidence/grp/candidate.json']
        self.assertEqual(self.check()[0], ['evidence/grp/candidate.json: relocated candidate has no recorded '
                                           'captured origins'])

    def test_facts_are_only_recorded_from_manifest_identical_bytes(self):
        (self.fetched / 'grp/log.txt').write_bytes(b'(kicad' b'_sch (version 2))')
        with self.assertRaisesRegex(ValueError, 'does not match the files manifest'):
            origins.record_relocated_facts(self.inventory, self.relocation, self.fetched, self.root)
        self.relocation['files_manifest_sha256'] = '0' * 64
        self.assertEqual(self.check()[0], ['evidence/relocation.json: '
                                           'files manifest digest does not match'])


if __name__ == '__main__':
    unittest.main()
