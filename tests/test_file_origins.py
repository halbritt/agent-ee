"""License declaration and file-origin inventory (AGENTEE-14).

The inventory proves classification and declared provenance, not anyone's
rights. These tests exercise the real repository and the main failure
boundaries on small synthetic trees.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('check_origins', ROOT / 'scripts/check_origins.py')
origins = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(origins)
REAL = json.loads((ROOT / 'docs/file-origins.json').read_text())
UNRESOLVED = 'unresolved-owner-decision-required'
PROPOSED = 'proposed-mit-pending-owner-approval'


def origin(status, **extra):
    return {'name': 'n', 'upstream_license': 'u', 'owner_decision': 'd', 'permission_status': status, **extra}


def inventory(rules, **changed):
    data = {
        'schema': 'agent-ee.file-origins/1',
        'kinds': REAL['kinds'],
        'origins': {'AE': origin(PROPOSED, capture_sources=['/requirements/']), 'AE-RUN': origin(PROPOSED),
                    'T1': origin(UNRESOLVED, capture_sources=['/fixture/']),
                    'T3': origin(UNRESOLVED, capture_sources=['/donor/'])},
        'rules': rules,
    }
    data['origins'].update(changed)
    return data


def rule(paths, origins_=('AE',), kind='owned-source', **extra):
    return {'paths': list(paths), 'kind': kind, 'origins': list(origins_), **extra}


class SyntheticTree:
    def __init__(self, files):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        for path, data in files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data if isinstance(data, bytes) else data.encode())
        self.paths = sorted(files)

    def check(self, data):
        return origins.check(self.root, data, self.paths)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.directory.cleanup()


class LicenseDeclarationTests(unittest.TestCase):
    def test_license_file_is_mit_with_third_party_scope(self):
        text = (ROOT / 'LICENSE').read_text()
        self.assertTrue(text.startswith('MIT License\n\nCopyright (c) 2026 '))
        self.assertIn('Permission is hereby granted, free of charge, to any person obtaining a copy', text)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND', text)
        scope = ' '.join(text.split('Scope of this license', 1)[1].split())
        for required in ('origins AE or AE-RUN and no other origin', 'not offered under this license',
                         'docs/file-origins.json', 'src/agent_ee/checks.py', 'FOME-MC'):
            self.assertIn(required, scope)

    def test_package_metadata_declares_mit(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
        self.assertEqual(project['license'], {'text': 'MIT'})

    def test_readme_declares_license_and_pending_status(self):
        text = (ROOT / 'README.md').read_text()
        section = ' '.join(text.split('\n## License\n', 1)[1].split())
        for required in ('[MIT License](LICENSE)', 'package metadata', '(docs/file-origins.json)',
                         '(docs/third-party-origins.md)', 'has not yet approved', 'FOME-MC', 'unresolved'):
            self.assertIn(required, section)


class RepositoryInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.errors, cls.summary = origins.check(ROOT)
        cls.files = {item['path']: item for item in cls.summary.get('inventory', [])}

    def test_every_tracked_file_is_classified_without_errors(self):
        self.assertEqual(self.errors, [])
        self.assertEqual(self.summary['files'], self.summary['classified'])
        self.assertGreater(self.summary['files'], 5000)

    def test_fome_mc_copies_and_adapted_logic_are_unresolved(self):
        for path in ('fixtures/rc/control/rc_filter.kicad_sch', 'fixtures/rc/control/rc_filter.kicad_pcb',
                     'fixtures/rc/control/fp-lib-table', 'fixtures/rc/seeded/rc_filter.kicad_sch',
                     'evidence/rc-verified/disposable/rc_filter.kicad_pcb',
                     'evidence/model-comparison/cases/width-a/native/rc_filter.kicad_pcb'):
            with self.subTest(path=path):
                self.assertEqual(self.files[path]['kind'], 'copied-design')
                self.assertIn('T1', self.files[path]['origins'])
                self.assertEqual(self.files[path]['permission_status'], UNRESOLVED)
        checks = self.files['src/agent_ee/checks.py']
        self.assertEqual((checks['kind'], checks['permission_status']), ('adapted-source', UNRESOLVED))
        self.assertIn('T2', checks['origins'])
        self.assertEqual(self.files['fixtures/rc/PROVENANCE.md']['permission_status'], PROPOSED)

    def test_donor_vendor_and_archive_material_is_unresolved(self):
        expected = {
            'references/TJA1051.pdf': 'T10',
            'docker/kicad-10.0-releases.asc': 'T12',
            'evidence/donor-final/packet.json': 'T3',
            'evidence/archives/donor-verified.tar.gz': 'T5',
            'evidence/archives/rc-final.tar.gz': 'T6',
            'evidence/model-baselines-final/codex/review-work/physical/experiments/gerbers/Polygonus-Universal-Base-F_Cu.gtl': 'T3',
            'evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_f0_port.cpp': 'T9',
            'evidence/model-baselines-final/codex/review-work/circuit/experiments/references/Polygonus_mcu.kicad_sch': 'T8',
            'benchmarks/seeded/expectations.json': 'T13',
            'fixtures/datasheet-pins/facts.json': 'T10',
        }
        for path, origin_id in expected.items():
            with self.subTest(path=path):
                self.assertIn(origin_id, self.files[path]['origins'])
                self.assertEqual(self.files[path]['permission_status'], UNRESOLVED)

    def test_only_agent_ee_origins_are_proposed_for_mit(self):
        for item in self.files.values():
            if item['permission_status'] != UNRESOLVED:
                self.assertLessEqual(set(item['origins']), origins.OWNED, item['path'])
        for key, value in REAL['origins'].items():
            if key not in origins.OWNED:
                self.assertEqual(value['permission_status'], UNRESOLVED, key)

    def test_walk_exclusions_mirror_gitignore(self):
        lines = {line.strip() for line in (ROOT / '.gitignore').read_text().splitlines()
                 if line.strip() and not line.startswith('#')}
        self.assertEqual(lines, origins.GITIGNORE_LINES)


class InventoryBoundaryTests(unittest.TestCase):
    def test_unclassified_and_unused_rule_paths_fail(self):
        with SyntheticTree({'src/a.py': 'x', 'notes.txt': 'x'}) as tree:
            errors, _ = tree.check(inventory([rule(['src/', 'scripts/'])]))
        self.assertIn('notes.txt: no rule classifies this file', errors)
        self.assertIn('rule 0: scripts/ selects no file', errors)

    def test_fully_shadowed_rule_path_fails(self):
        with SyntheticTree({'src/a.py': 'x'}) as tree:
            errors, _ = tree.check(inventory([rule(['src/']), rule(['src/a.py'])]))
        self.assertEqual(errors, ['rule 0: src/ selects no file'])

    def test_equally_specific_rules_are_ambiguous(self):
        with SyntheticTree({'src/a.py': 'x'}) as tree:
            errors, _ = tree.check(inventory([rule(['src/']), rule(['src/'], ['T1'])]))
        self.assertIn('src/a.py: ambiguous between rules [0, 1]', errors)

    def test_most_specific_rule_wins_and_prefixes_respect_directories(self):
        files = {'fixtures/rc/a.kicad_sch': 'x', 'fixtures/rc/b.md': 'x', 'fixtures/rc/c.md': 'x',
                 'fixtures/rc-extra/d.md': 'x', 'fixtures/e.md': 'x'}
        rules = [rule(['fixtures/']), rule(['fixtures/rc/'], ['T1'], 'copied-design'),
                 rule(['fixtures/rc/'], ['T1'], 'copied-design', suffixes=['.kicad_sch']),
                 rule(['fixtures/rc/c.md'], kind='owned-metadata')]
        with SyntheticTree(files) as tree:
            errors, summary = tree.check(inventory(rules))
        self.assertEqual(errors, [])
        chosen = {item['path']: item['rule'] for item in summary['inventory']}
        self.assertEqual(chosen, {'fixtures/e.md': 0, 'fixtures/rc-extra/d.md': 0, 'fixtures/rc/b.md': 1,
                                  'fixtures/rc/a.kicad_sch': 2, 'fixtures/rc/c.md': 3})

    def test_risky_file_type_under_owned_prefix_needs_exact_review(self):
        files = {'docs/guide.md': 'text', 'docs/datasheet.pdf': b'%PDF-1.7'}
        with SyntheticTree(files) as tree:
            errors, _ = tree.check(inventory([rule(['docs/'], kind='owned-documentation')]))
            self.assertEqual(errors, ['docs/datasheet.pdf: file type needs a third-party origin '
                                      'or an exact reviewed rule'])
            reviewed = [rule(['docs/'], kind='owned-documentation'),
                        rule(['docs/datasheet.pdf'], kind='owned-documentation', reviewed='Generated here.')]
            self.assertEqual(tree.check(inventory(reviewed))[0], [])
            third_party = [rule(['docs/'], kind='owned-documentation'),
                           rule(['docs/datasheet.pdf'], ['T1'], 'third-party-document')]
            self.assertEqual(tree.check(inventory(third_party))[0], [])

    def test_owned_text_with_third_party_markers_fails(self):
        cases = {'(kicad_pcb (version 20240108)': 'KiCad design text',
                 '(footprint "R_0805"': 'KiCad design text',
                 'Copyright (c) 2017 Vendor. All rights reserved.': 'rights notice',
                 '-----BEGIN PGP PUBLIC KEY BLOCK-----': 'PGP block',
                 'fetched from Polygonus master': 'third-party project name'}
        for text, reason in cases.items():
            with self.subTest(reason=reason), SyntheticTree({'docs/a.md': text}) as tree:
                errors, _ = tree.check(inventory([rule(['docs/'], kind='owned-documentation')]))
                self.assertEqual(len(errors), 1)
                self.assertIn(reason, errors[0])
        with SyntheticTree({'docs/a.md': 'Mentions FOME-MC and KiCad only.'}) as tree:
            self.assertEqual(tree.check(inventory([rule(['docs/'], kind='owned-documentation')]))[0], [])

    def test_reviewed_rules_must_name_exact_paths(self):
        with SyntheticTree({'docs/a.pdf': 'x'}) as tree:
            errors, _ = tree.check(inventory([rule(['docs/'], reviewed='all fine')]))
        self.assertEqual(errors, ['rule 0: reviewed rules must list exact paths'])

    def test_confirmation_requires_owner_record(self):
        with SyntheticTree({'fixture/a.kicad_sch': 'x'}) as tree:
            rules = [rule(['fixture/'], ['T1'], 'copied-design')]
            errors, _ = tree.check(inventory(rules, T1=origin('owner-confirmed')))
            self.assertEqual(errors, ['origin T1: owner-confirmed requires owner_record'])
            record = origin('owner-confirmed', owner_record='2026-10-18 owner approved MIT in AGENTEE-14')
            errors, summary = tree.check(inventory(rules, T1=record))
            self.assertEqual(errors, [])
            self.assertEqual(summary['inventory'][0]['permission_status'], 'owner-confirmed')

    def test_third_party_origin_cannot_be_proposed_for_mit(self):
        with SyntheticTree({'src/a.py': 'x'}) as tree:
            errors, _ = tree.check(inventory([rule(['src/'])], T3=origin(PROPOSED)))
        self.assertEqual(errors, ['origin T3: third-party origin cannot be proposed under MIT'])

    def test_unknown_origin_kind_status_and_unsafe_paths_fail(self):
        bad = [rule(['../outside']), rule(['/abs/']), rule(['src/'], ['T99']), rule(['src/'], kind='mystery')]
        with SyntheticTree({'src/a.py': 'x'}) as tree:
            errors, _ = tree.check(inventory(bad, T1=origin('maybe')))
        for expected in ("rule 0: path '../outside' is not a normalized relative path",
                         "rule 1: path '/abs/' is not a normalized relative path",
                         "rule 2: unknown origin 'T99'", "rule 3: unknown kind 'mystery'",
                         "origin T1: unknown permission_status 'maybe'"):
            self.assertIn(expected, errors)

    def test_capture_from_undeclared_origin_fails(self):
        manifest = {'sources': {'project/board.kicad_pcb': '/home/x/donor/board.kicad_pcb',
                                'requirements/r.md': '/repo/requirements/r.md'}}
        files = {'evidence/run/candidate.json': json.dumps(manifest), 'evidence/run/log.txt': 'x'}
        with SyntheticTree(files) as tree:
            errors, _ = tree.check(inventory([rule(['evidence/run/'], ['AE-RUN', 'T1'], 'retained-output')]))
            self.assertEqual(errors, ["evidence/run/candidate.json: captured inputs come from ['T3'], not declared by rule 0"])
            correct = [rule(['evidence/run/'], ['AE-RUN', 'T3'], 'retained-output')]
            self.assertEqual(tree.check(inventory(correct))[0], [])

    def test_unrecognized_captured_source_fails(self):
        manifest = {'sources': {'project/x.kicad_sch': '/somewhere/else/x.kicad_sch'}}
        with SyntheticTree({'evidence/run/candidate.json': json.dumps(manifest)}) as tree:
            errors, _ = tree.check(inventory([rule(['evidence/run/'], ['AE-RUN', 'T1'], 'retained-output')]))
        self.assertEqual(errors, ['evidence/run/candidate.json: captured project/x.kicad_sch from unrecognized '
                                  'source /somewhere/else/x.kicad_sch'])

    def test_archive_must_declare_origins_of_its_directory_and_captures(self):
        manifest = {'sources': {'project/a.kicad_pcb': '/x/donor/a.kicad_pcb'}}
        files = {'evidence/run/candidate.json': json.dumps(manifest), 'evidence/run/a.kicad_pcb': 'x',
                 'evidence/archives/run.tar.gz': b'\x1f\x8b'}
        rules = [rule(['evidence/run/'], ['AE-RUN', 'T3'], 'retained-output'),
                 rule(['evidence/run/a.kicad_pcb'], ['T1'], 'copied-design'),
                 rule(['evidence/archives/run.tar.gz'], ['AE-RUN', 'T3'], 'evidence-archive')]
        with SyntheticTree(files) as tree:
            errors, _ = tree.check(inventory(rules))
            self.assertEqual(errors, ["evidence/archives/run.tar.gz: holds evidence/run/ material from ['T1'] not declared by rule 2"])
            rules[2]['origins'].append('T1')
            self.assertEqual(tree.check(inventory(rules))[0], [])

    def test_walk_without_git_applies_gitignore_exclusions(self):
        files = {'src/a.py': 'x', 'src/__pycache__/a.cpython-312.pyc': 'x', 'src/b.pyc': 'x',
                 'build/out.txt': 'x', 'pkg.egg-info/PKG-INFO': 'x', 'evidence/run/inputs/project/x.kicad_sch': 'x',
                 'evidence/run/home/.cache': 'x', 'evidence/run/kicad-config/sym-lib-table': 'x',
                 'evidence/run/report.json': 'x', 'fixtures/inputs/kept.txt': 'x'}
        with SyntheticTree(files) as tree:
            self.assertEqual(origins.tracked_files(tree.root),
                             ['evidence/run/report.json', 'fixtures/inputs/kept.txt', 'src/a.py'])


if __name__ == '__main__':
    unittest.main()
