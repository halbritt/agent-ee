"""Evidence relocated to the owner's store (AGENTEE-20); no network or credentials used.

The tree keeps only what tests, fixture tools and linked summaries read. These
tests pin that the kept bytes are exactly the manifest's, that nothing else of
a relocated group lingers, and that absent bytes produce typed refusals. The
retained-byte checks list files from Git when present and otherwise by the
check_origins walk, so a Git-absent tree (the release image) is verified, never
skipped.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
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


def retained_problems(root):
    """Differences between the tree's evidence files and the retained manifest bytes."""
    present = [p for p in origins.tracked_files(root) if p.startswith('evidence/')]
    problems = []
    for group, files in sorted(MANIFEST['groups'].items()):
        relocated = group in RELOCATION['relocated_groups']
        expected = RELOCATION['retained_in_tree'].get(group, []) if relocated else sorted(files['files'])
        listed = sorted(p for p in present if p.startswith(f'evidence/{group}/'))
        if listed != sorted(f'evidence/{group}/{p}' for p in expected):
            problems.append(f'{group}: files {sorted(set(listed) ^ {f"evidence/{group}/{p}" for p in expected})} '
                            'differ from the retained manifest set')
        for name in expected:
            path = Path(root) / 'evidence' / group / name
            record = files['files'][name]
            data = path.read_bytes() if path.is_file() else b''
            if (len(data), hashlib.sha256(data).hexdigest()) != (record['bytes'], record['sha256']):
                problems.append(f'{group}/{name}: bytes differ from the manifest')
    return problems


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
        self.assertEqual(retained_problems(ROOT), [])

    def test_relocated_paths_stay_classified_from_recorded_facts(self):
        errors, summary = origins.check(ROOT)
        self.assertEqual(errors, [])
        relocated = sum(len(MANIFEST['groups'][g]['files']) - len(RELOCATION['retained_in_tree'].get(g, []))
                        for g in RELOCATION['relocated_groups'])
        self.assertEqual(summary['relocated'], relocated)
        self.assertEqual(RELOCATION['origin_facts']['basis_sha256'],
                         origins.facts_basis(json.loads((ROOT / 'docs/file-origins.json').read_text())))


class GitAbsentTreeTests(unittest.TestCase):
    """A copy of the tracked tree without .git, as in the release image, runs every check without skipping."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.copy = Path(cls.directory.name) / 'tree'
        for path in origins.tracked_files(ROOT):
            target = cls.copy / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
        assert not (cls.copy / '.git').exists()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_record_tests(self):
        environment = dict(os.environ, AGENT_EE_REQUIRE_TOOLCHAIN='1', PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, '-m', 'unittest', '-v', 'tests.test_evidence_relocation.RelocationRecordTests'],
                              cwd=self.copy, capture_output=True, text=True, env=environment)

    def test_strict_mode_verifies_retained_bytes_without_git(self):
        result = self.run_record_tests()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Ran 3 tests', result.stderr)
        self.assertNotIn('skip', result.stderr.lower())
        self.assertEqual(retained_problems(self.copy), [])

    def test_stray_changed_or_missing_bytes_fail_without_git(self):
        group = RELOCATION['relocated_groups'][0]
        kept = next(iter(RELOCATION['retained_in_tree'].items()))
        kept_path = self.copy / 'evidence' / kept[0] / kept[1][0]
        original = kept_path.read_bytes()
        stray = self.copy / 'evidence' / group / 'stray.txt'
        for mutation in ('stray', 'changed', 'missing'):
            with self.subTest(mutation=mutation):
                if mutation == 'stray':
                    stray.write_bytes(b'relocated bytes left behind')
                elif mutation == 'changed':
                    kept_path.write_bytes(original + b' ')
                else:
                    kept_path.unlink()
                try:
                    self.assertNotEqual(retained_problems(self.copy), [])
                    result = self.run_record_tests()
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertIn('test_tree_holds_exactly_the_retained_manifest_bytes', result.stderr)
                finally:
                    stray.unlink(missing_ok=True)
                    kept_path.write_bytes(original)
        self.assertEqual(retained_problems(self.copy), [])


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
        self.assertEqual(facts['content_markers'], {'evidence/grp/candidate.json': [], 'evidence/grp/kept.txt': [],
                                                    'evidence/grp/log.txt': ['KiCad design text']})
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
        self.assertEqual(self.check(), (['evidence/relocation.json: origin_facts captured_origins lacks 1 '
                                         'relocated file(s), first evidence/grp/candidate.json'], {}))

    def test_incomplete_or_malformed_facts_refuse_instead_of_reading_clean(self):
        # log.txt holds KiCad design text; under an owned-only rule that is an origin error.
        self.inventory['rules'].insert(0, rule(['evidence/grp/log.txt'], ['AE-RUN'], 'owned-metadata'))
        self.inventory['rules'][1]['paths'] = ['evidence/grp/candidate.json', 'evidence/grp/kept.txt']
        self.assertEqual(self.check()[0], ['evidence/grp/log.txt: KiCad design text needs a third-party origin '
                                           'or an exact reviewed rule'])
        recorded = json.loads(json.dumps(self.relocation['origin_facts']))

        def drop(table, key=None):
            return lambda facts: facts[table].pop(key) if key else facts.pop(table)

        def put(table, key, value):
            return lambda facts: facts[table].__setitem__(key, value)

        mutations = {
            'no content_markers table': drop('content_markers'),
            'no log.txt marker entry': drop('content_markers', 'evidence/grp/log.txt'),
            'no captured_origins table': drop('captured_origins'),
            'no schema': drop('schema'),
            'old schema': lambda facts: facts.__setitem__('schema', 'agent-ee.relocated-origin-facts/1'),
            'other files manifest': lambda facts: facts.__setitem__('files_manifest_sha256', '0' * 64),
            'unknown key': lambda facts: facts.__setitem__('note', 'x'),
            'null markers': put('content_markers', 'evidence/grp/log.txt', None),
            'string markers': put('content_markers', 'evidence/grp/log.txt', 'KiCad design text'),
            'unknown marker': put('content_markers', 'evidence/grp/log.txt', ['clean']),
            'unsorted markers': put('content_markers', 'evidence/grp/log.txt', ['PDF header', 'KiCad design text']),
            'path outside groups': put('content_markers', 'evidence/other/x', []),
            'unknown origin': put('captured_origins', 'evidence/grp/candidate.json', ['T99']),
            'table not object': lambda facts: facts.__setitem__('content_markers', []),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                self.relocation['origin_facts'] = json.loads(json.dumps(recorded))
                mutate(self.relocation['origin_facts'])
                errors, summary = self.check()
                self.assertTrue(errors)
                self.assertEqual(summary, {})
                self.assertTrue(all(e.startswith('evidence/relocation.json: origin') or
                                    e.startswith('evidence/relocation.json: relocated origin') for e in errors), errors)
        for facts in (None, [], {}):
            with self.subTest(facts=facts):
                self.relocation['origin_facts'] = facts
                self.assertEqual(self.check(), ([f'evidence/relocation.json: origin_facts schema must be '
                                                 f'{origins.FACTS_SCHEMA}; re-record them with '
                                                 'scripts/check_origins.py --relocated-facts'], {}))
        del self.relocation['origin_facts']
        self.assertTrue(self.check()[0])

    def test_retained_file_facts_must_agree_with_its_bytes(self):
        self.relocation['origin_facts']['content_markers']['evidence/grp/kept.txt'] = ['PDF header']
        self.assertEqual(self.check()[0], ["evidence/relocation.json: origin_facts content_markers for retained "
                                           "evidence/grp/kept.txt disagree with its bytes: ['PDF header'] != []"])

    def test_facts_are_only_recorded_from_manifest_identical_regular_files(self):
        (self.fetched / 'grp/kept.txt').unlink()
        with self.assertRaisesRegex(ValueError, 'missing or not a regular file'):
            origins.record_relocated_facts(self.inventory, self.relocation, self.fetched, self.root)
        (self.fetched / 'grp/kept.txt').symlink_to(self.root / 'evidence/grp/kept.txt')
        with self.assertRaisesRegex(ValueError, 'missing or not a regular file'):
            origins.record_relocated_facts(self.inventory, self.relocation, self.fetched, self.root)
        (self.fetched / 'grp/kept.txt').unlink()
        (self.fetched / 'grp/kept.txt').write_bytes(b'kept')
        (self.fetched / 'grp/log.txt').write_bytes(b'(kicad' b'_sch (version 2))')
        with self.assertRaisesRegex(ValueError, 'does not match the files manifest'):
            origins.record_relocated_facts(self.inventory, self.relocation, self.fetched, self.root)
        self.relocation['files_manifest_sha256'] = '0' * 64
        self.assertEqual(self.check()[0], ['evidence/relocation.json: '
                                           'files manifest digest does not match'])


if __name__ == '__main__':
    unittest.main()
