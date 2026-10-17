"""License declaration and third-party-origin inventory.

The inventory (docs/third-party-origins.md) must name every tracked file that
may originate in another project, keep an explicit publication status for each
origin, and never mark an origin resolved without a recorded owner decision.
"""
import re
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / 'docs' / 'third-party-origins.md'
PENDING = 'requires owner confirmation before publication'
CONFIRMED = re.compile(r'Owner confirmed \d{4}-\d{2}-\d{2} \(record: [^)]+\)')
ENTRY = re.compile(r'^- `([^`]+)`', re.M)
ORIGIN_SECTION = re.compile(r'^## (T\d+) [^\n]*\n(.*?)(?=^## |\Z)', re.M | re.S)
STATUS = re.compile(r'Publication status: ([^\n]*(?:\n  [^\n]*)*)')

FOME_MC_FIXTURE = [f'fixtures/rc/{variant}/{name}'
                   for variant in ('control', 'seeded')
                   for name in ('fp-lib-table', 'rc_filter.kicad_pcb', 'rc_filter.kicad_pro',
                                'rc_filter.kicad_sch', 'sym-lib-table')]

# Tracked files of these kinds may come from another project and must be triaged.
CANDIDATE_SUFFIXES = (
    '.pdf', '.html', '.htm', '.tmp', '.tar.gz', '.tgz', '.zip', '.png', '.svg', '.jpg', '.jpeg',
    '.kicad_sch', '.kicad_pcb', '.kicad_pro', '.kicad_prl', '.kicad_sym', '.kicad_mod',
    '.kicad_dru', '.kicad_wks', '.lib', '.dcm', '.mod', '.drl', '.rpt', '.gbr', '.gbrjob',
    '.gtl', '.gbl', '.gts', '.gbs', '.gto', '.gbo', '.gtp', '.gbp', '.gm1', '.g1', '.g2',
    '.g3', '.g4', '.c', '.cpp', '.h')
LIBRARY_TABLES = {'sym-lib-table', 'fp-lib-table'}
REFERENCE_DIR = re.compile(r'/experiments/(?:[^/]+/)*(?:ref_[^/]*|refs|references|fetch|polygonus)/',
                           re.I)
CONTENT_MARKERS = re.compile(
    rb'polygonus|universal 112|wbo-can-trans|\(kicad_sch|\(kicad_pcb|all rights reserved|%PDF-',
    re.I)


def inventory_entries(text):
    return ENTRY.findall(text)


def is_candidate(path, content):
    name = path.rsplit('/', 1)[-1]
    return (path.lower().endswith(CANDIDATE_SUFFIXES) or name in LIBRARY_TABLES
            or bool(REFERENCE_DIR.search(path)) or bool(CONTENT_MARKERS.search(content)))


def is_covered(path, entries):
    return any(path == entry or (entry.endswith('/') and path.startswith(entry))
               for entry in entries)


def stale_entries(entries, tracked):
    return [entry for entry in entries if not any(is_covered(path, [entry]) for path in tracked)]


def invalid_statuses(text):
    """Return origin IDs whose publication status is missing or not an allowed value."""
    invalid = []
    for origin, body in ORIGIN_SECTION.findall(text):
        match = STATUS.search(body)
        status = ' '.join(match.group(1).split()) if match else ''
        if not (status.startswith(PENDING) or CONFIRMED.match(status)):
            invalid.append(origin)
    return invalid


def tracked_files():
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True)
    return [path.decode() for path in result.stdout.split(b'\0') if path]


class LicenseDeclarationTests(unittest.TestCase):
    def test_license_file_is_mit_with_third_party_exclusion(self):
        text = (ROOT / 'LICENSE').read_text()
        self.assertTrue(text.startswith('MIT License\n'))
        self.assertRegex(text, r'Copyright \(c\) 2026 .*halbritt')
        self.assertIn('Permission is hereby granted, free of charge, to any person obtaining a copy',
                      text)
        self.assertIn('The above copyright notice and this permission notice shall be included in all\n'
                      'copies or substantial portions of the Software.', text)
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND', text)
        scope = text.split('Scope of this license', 1)[1]
        self.assertIn('docs/third-party-origins.md', scope)
        self.assertIn('not offered under this license', scope)
        self.assertIn(PENDING, ' '.join(scope.split()))

    def test_package_metadata_declares_mit(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
        self.assertEqual(project['license'], {'text': 'MIT'})

    def test_readme_declares_license_and_points_to_inventory(self):
        text = (ROOT / 'README.md').read_text()
        self.assertIn('[MIT License](LICENSE)', text)
        self.assertIn('(docs/third-party-origins.md)', text)


class ThirdPartyInventoryTests(unittest.TestCase):
    def setUp(self):
        self.text = INVENTORY.read_text()
        self.sections = dict(ORIGIN_SECTION.findall(self.text))

    def test_fome_mc_fixture_and_adapted_code_are_listed_as_unresolved(self):
        fixture = self.sections['T1']
        for path in FOME_MC_FIXTURE:
            self.assertIn(path, inventory_entries(fixture))
        self.assertIn('src/agent_ee/checks.py', inventory_entries(self.sections['T2']))
        for origin in ('T1', 'T2', 'T3'):
            self.assertIn(f'Publication status: {PENDING}', self.sections[origin])
        self.assertIn('scopes/donor.json', inventory_entries(self.sections['T3']))
        self.assertIn('references/TJA1051.pdf', inventory_entries(self.sections['T10']))

    def test_every_origin_has_an_explicit_publication_status(self):
        self.assertGreaterEqual(len(self.sections), 11)
        self.assertEqual(invalid_statuses(self.text), [])

    def test_status_cannot_be_resolved_without_owner_record(self):
        sample = ('## T1 a\nPublication status: cleared for MIT.\n'
                  '## T2 b\nNo status line.\n'
                  f'## T3 c\nPublication status: {PENDING}.\n'
                  '## T4 d\nPublication status: Owner confirmed 2026-10-20 (record: AGENTEE-14 comment).\n'
                  '## T5 e\nPublication status: Owner confirmed (verbally).\n')
        self.assertEqual(invalid_statuses(sample), ['T1', 'T2', 'T5'])

    def test_tracked_third_party_candidates_are_all_listed(self):
        tracked = tracked_files()
        entries = inventory_entries(self.text)
        self.assertEqual(stale_entries(entries, tracked), [])
        uncovered = []
        for path in tracked:
            content = b'' if path.lower().endswith(CANDIDATE_SUFFIXES) else (ROOT / path).read_bytes()
            if is_candidate(path, content) and not is_covered(path, entries):
                uncovered.append(path)
        self.assertEqual(uncovered, [])

    def test_unlisted_candidate_and_stale_entry_are_detected(self):
        entries = ['evidence/rc-final/', 'references/TJA1051.pdf']
        new_datasheet = 'evidence/new-run/review-work/circuit/experiments/datasheet.pdf'
        fetched = 'evidence/new-run/review-work/circuit/experiments/refs/notes.md'
        self.assertTrue(is_candidate(new_datasheet, b''))
        self.assertTrue(is_candidate(fetched, b'plain notes'))
        self.assertTrue(is_candidate('docs/new.md', b'copied from Polygonus'))
        self.assertFalse(is_candidate('docs/new.md', b'written here'))
        self.assertFalse(is_covered(new_datasheet, entries))
        self.assertFalse(is_covered('evidence/rc-final-2/REPORT.md', entries))
        self.assertTrue(is_covered('evidence/rc-final/seed/REPORT.md', entries))
        self.assertEqual(stale_entries(entries + ['references/missing.pdf'],
                                       ['references/TJA1051.pdf', 'evidence/rc-final/seed/REPORT.md']),
                         ['references/missing.pdf'])


if __name__ == '__main__':
    unittest.main()
